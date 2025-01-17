import sys
import time
import warnings

from pprint import pformat
from pathlib import Path

import numpy as np

import ase.io
from ase.md.langevin import Langevin
from ase import units
from ase.calculators.calculator import PropertyNotImplementedError

from ACEHAL.fit import fit
from ACEHAL.basis import define_basis
from ACEHAL.bias_calc import BiasCalculator, TauRelController
from ACEHAL.optimize_basis import optimize

from ACEHAL.dyn_utils import SwapMC, CellMC, HALMonitor, HALTolExceeded
from ACEHAL import viz

def HAL(fit_configs, traj_configs, basis_source, solver, fit_kwargs, n_iters, ref_calc,
         traj_len, dt_fs, tol, tau_rel, T_K, P_GPa=None, cell_fixed_shape=False, T_timescale_fs=100, tol_eps=0.1, tau_hist=100,
         cell_step_interval=10, swap_step_interval=0, cell_step_mag=0.01,
         default_basis_info=None, basis_optim_kwargs=None, basis_optim_interval=None,
         file_root=None, traj_interval=10, test_configs=[], test_fraction=0.0, atom_sigma=0.0):
    """Iterate with hyperactive learning

    Parameters
    ----------
    fit_configs: list(Atoms)
        initial fitting configs
    traj_configs: list(Atoms)
        configs to start trajectories from
    basis_source: str
        module with basis or text string with julia source to construct basis
    solver: sklearn-compatible LinearSolver
        solvers for model parameter design matrix linear problem
    fit_kwargs: dict
        User (not HAL provided) keyword args for `fit.fit()`, in particular "E0s", "data_keys", and "weights".
        HAL will provide "atoms_list", "solver", "B_len_norm", and "return_linear_problem".
        NOTE: this function explicitly uses "data_keys" and "E0s" fit_kwargs entries - should those be passed in explicitly?
            If so, should HAL add them to fit_kwargs, or should the caller?
    n_iters: int
        number of HAL iterations
    ref_calc: Calculator
        calculator for reference quantities, or None for dry run

    traj_len: int
        max len of trajectories (unless tau_rel exceeded) (overridable in Atoms.info)
    dt_fs: float
        time step (in fs) for dynamics (overridable in Atoms.info)
    tol: float
        tolerance for triggering HAL in fractional force error. If negative, save first config that
        exceeds tol but continue trajectory to full traj_len (overridable in Atoms.info)
    tau_rel: float / tuple(float, float)
        strength of bias forces relative to unbiased forces, fixed or range for ramp (overridable in Atoms.info)
    T_K: float / tuple(float, float)
        temperature (in K) for Langevin dynamics, fixed or range for ramp (overridable in Atoms.info)
    P_GPa: float / tuple(float, float), default None
        pressure (in GPa) for dynamics, fixed or range for ramp, None for fixed cell (overridable in Atoms.info)
    cell_fixed_shape: bool, default False
        variable cell only vary volume, not shape
    T_timescale_fs: float, default 100.0
        time scale (in fs) for Langevin dynamics (overridable in Atoms.info)
    tol_eps: float, default 0.1
        regularization epsilon to add to force denominator when computing relative force error for HAL tolerance (overridable in Atoms.info)
    tau_hist: int, default 100
        number of timesteps over which to smooth force magnitudes for HAL criterion tau_rel (overridable in Atoms.info)

    cell_step_interval: int, default 25
        interval for attempts of Monte Carlo cell steps
    swap_step_interval: int, default 0
        interval for attempts of atom swap steps
    cell_step_mag: float, default 0.01
        magnitude of perturbations for cell MC steps
    default_basis_info: dict, default None
        default parameters for basis (ignored if optimizing basis)
    basis_optim_kwargs: dict, default None
        User provided keyword arguments used to optimize basis with for `optimize_basis.optimize()`,
        or None for no optimization. Usually includes "n_trials", "fixed_basis_info", "optimize_params",
        "max_basis_len", and perhaps "score" or "seed".  Arguments that will be provided by HAL are "solver",
        "fitting_db", "basis_kwargs", and "fit_kwargs".
    basis_optim_interval: int, default None
        interval (in HAL iterations, whether or not they generate configs added to fitting database)
        between re-optimizing basis, None to only optimize in initial step.
    file_root: str / Path, default None
        base part of path to all saved files (trajectory, potential, new config, plots)
    traj_interval: int, default 10
        interval between trajectory snapshots
    test_configs: list(Atoms), default []
        configs for test set
    test_fraction: float, default 0.0
        fraction of configs to select (stochastic) for testing set

    Returns
    -------
    new_fit_configs: list(Atoms) with configurations added by HAL
    basis_info: dict with info for constructing last basis used
    if test_fraction > 0.0:
        new_test_configs: list(Atoms) with new test set configurations
    """

    default_traj_params = {"traj_len": traj_len,
                           "dt_fs": dt_fs,
                           "tol": tol,
                           "tau_rel": tau_rel,
                           "T_K": T_K,
                           "P_GPa": P_GPa,
                           "cell_fixed_shape": cell_fixed_shape,
                           "T_timescale_fs": T_timescale_fs,
                           "tol_eps": tol_eps,
                           "tau_hist": tau_hist}

    # prepare file root of type Path
    if file_root is None:
        file_root = Path()
    file_root = Path(file_root)
    if file_root.is_dir():
        file_root = file_root / "HAL"

    def _HAL_label(it):
        n_dig = int(np.log10(n_iters)) + 1
        s = "it_{it:0" + str(n_dig) + "d}"
        return s.format(it=it)

    # initial basis
    if default_basis_info is not None:
        basis_info = default_basis_info.copy()
        B_len_norm = define_basis(default_basis_info, basis_source)
    elif basis_optim_kwargs is not None:
        t0 = time.time()
        basis_info = _optimize_basis(fit_configs, basis_source, solver, fit_kwargs, basis_optim_kwargs)
        B_len_norm = define_basis(basis_info, basis_source)
        print("TIMING initial_basis_optim", time.time() - t0)
    else:
        raise ValueError("One of default_basis_info and basis_optim_kwargs must be provided")

    print("HAL initial basis", basis_info)

    # initial fit
    t0 = time.time()



    committee_calc = _fit(fit_configs, solver, fit_kwargs, B_len_norm, file_root, _HAL_label(0))
    print("TIMING initial_fit", time.time() - t0)
    error_configs = [("fit", fit_configs)]
    if len(test_configs) > 0 or test_fraction > 0:
        error_configs.append(("test", test_configs))
    print("INITIAL FIT ERROR TABLE")
    print(viz.error_table(error_configs, committee_calc, fit_kwargs["data_keys"]))
    sys.stdout.flush()

    # prepare lists for new configs
    new_fit_configs = []
    new_test_configs = []

    for iter_HAL in range(n_iters):
        HAL_label = _HAL_label(iter_HAL)

        # pick config to start trajectory from
        seed_ind = iter_HAL % len(traj_configs)
        traj_config = traj_configs[seed_ind].copy()
        traj_config.set_velocities(np.zeros(traj_config.positions.shape))

        # set parameters for this run based on defaults, optionally overriden by traj_config.info["HAL_traj_params"]
        traj_params = default_traj_params.copy()
        traj_params.update(traj_config.info.get("HAL_traj_params", {}))
        print("HAL iter", iter_HAL, "using params", traj_params)
        traj_len = traj_params["traj_len"]
        dt_fs = traj_params["dt_fs"]
        tol = traj_params["tol"]
        tau_rel = traj_params["tau_rel"]
        T_K = traj_params["T_K"]
        P_GPa = traj_params["P_GPa"]
        cell_fixed_shape = traj_params["cell_fixed_shape"]
        T_timescale_fs = traj_params["T_timescale_fs"]
        tol_eps = traj_params["tol_eps"]
        tau_hist = traj_params["tau_hist"]

        # delete existing reference calculation fields from info and arrays
        for d in [traj_config.info, traj_config.arrays]:
            for k in list(d.keys()):
                if k in fit_kwargs['data_keys'].values():
                    del d[k]
        traj_config.calc = BiasCalculator(committee_calc, 0.0)
        tau_rel_control = TauRelController(tau_rel, tau_hist)

        # attachment to monitor HAL tolerance, save data and annotated trajectory
        if traj_interval > 0:
            traj_filename = file_root.parent / (file_root.name + f".traj.{HAL_label}.extxyz")
        else:
            traj_filename = None
        hal_monitor = HALMonitor(traj_config, tol, tol_eps, tau_rel_control, traj_file=traj_filename, traj_interval=traj_interval)

        def _make_ramps(*args, n_stages=20):
            if not any([isinstance(arg, (tuple, list)) for arg in args]):
                n_stages = 1

            settings_out = []
            for setting in args:
                if isinstance(setting, (tuple, list)):
                    if len(setting) == 2:
                        settings_out.append(np.linspace(setting[0], setting[1], n_stages))
                    elif len(setting) == 3:
                        Ti = setting[0]
                        Tf = setting[1]
                        ramp_time = setting[2]
                        n_ramp = round(ramp_time/traj_len)
                        ramp_Ts = [Ti + (Tf-Ti)/n_ramp * i  if i < n_ramp else Tf for i in range(0, nsteps)]
                        settings.out.append(rnp.array(ramp_Ts))
                    #assert len(setting) == 2, "Got ramp for item with other than 2 elements {setting}"

                else:
                    settings_out.append([setting] * n_stages)

            return n_stages, settings_out

        # set up T and P ramps
        n_stages, (ramp_tau_rels, ramp_Ts, ramp_Ps) = _make_ramps(tau_rel, T_K, P_GPa, n_stages=50)

        t0 = time.time()
        # run dynamics for entire T ramp
        try:
            for tau_rel_cur, T_K_cur, P_GPa_cur in zip(ramp_tau_rels, ramp_Ts, ramp_Ps):
                traj_config.info["HAL_tau_rel"] = tau_rel_cur
                traj_config.info["HAL_T_K"] = T_K_cur
                if P_GPa_cur is not None:
                    traj_config.info["HAL_P_GPa"] = P_GPa_cur

                if P_GPa_cur is not None:
                    # attachment to do cell steps
                    # E = N B strain^2
                    # strain = sqrt(E / (B N))
                    # aim for cell_step_mag for 500 K and 40 atoms
                    # NOTE: need to make sure this actually scales sensibly in practice.  Maybe collect acceptance statistics?
                    cell_step_mag_use = cell_step_mag * np.sqrt((T_K_cur / 500.0) / (len(traj_config) / 40))
                    cell_mc = CellMC(traj_config, T_K_cur, P_GPa_cur, mag=cell_step_mag_use, fixed_shape=cell_fixed_shape)
                else:
                    cell_mc = None

                # set up tau controller for this section of ramp
                tau_rel_control.set_tau_rel(tau_rel_cur)

                # set up dynamics for this section of ramp
                dyn = Langevin(traj_config, dt_fs * ase.units.fs, temperature_K=T_K_cur, friction=(1/(T_timescale_fs * ase.units.fs)))

                # attach monitor and cell steps
                dyn.attach(hal_monitor)
                if cell_mc is not None:
                    dyn.attach(cell_mc, interval=cell_step_interval)

                if swap_step_interval > 0:
                    swap_mc = SwapMC(traj_config, T_K_cur)
                    dyn.attach(swap_mc, interval=swap_step_interval)

                # run trajectory for this section of T ramp
                dyn.run(traj_len // len(ramp_Ts))
                sys.stdout.flush()

                # mark restart so next call from next dyn.run skips first config
                hal_monitor.mark_restart()

                if cell_mc is not None:
                    print("HAL ramp step for tau_rel", tau_rel_cur, "T_K", T_K_cur, "P_GPa", P_GPa_cur,
                          "accepted", cell_mc.accept[0], "/", cell_mc.accept[1], "=", cell_mc.accept[0] / cell_mc.accept[1])

        except HALTolExceeded:
            pass
        print("TIMING trajectory", time.time() - t0)

        # save HAL-selected config
        if hal_monitor.HAL_trigger_config is not None:
            new_config = hal_monitor.HAL_trigger_config
        else:
            # no trigger, just grab most recent config
            new_config = traj_config

        # If trajectory was triggered by HAL tolerance, config was not already written
        # and will be written here.  If trajectory completed normally and last config was
        # already set to be written according to traj_interval, it was already written and
        # this call should know and not actually write it again.
        hal_monitor.write_final_config(new_config)

        plot_traj_file = file_root.parent / (file_root.name + f".run_data.{HAL_label}.pdf")
        trigger_data = {"criterion": (hal_monitor.HAL_trigger_step, np.abs(tol))}
        viz.plot_HAL_traj_data(hal_monitor.run_data, trigger_data, plot_traj_file)

        print(f"HAL iter {iter_HAL} got config with " +
              " , ".join([f"{k} : {v}" for k, v in new_config.info.items() if k.startswith('HAL_') and k != "HAL_traj_params"]) +
              f" traj_len {traj_len}")
        sys.stdout.flush()

        HAL_step = new_config.info["HAL_step"]
        print("jpd47 HAL_step is {}".format(HAL_step))
        # jpd47 skip reference calculation and don't include configuration again if iter_HAL == 0
        if ref_calc is not None:
            if HAL_step == 0:
                new_config = traj_configs[seed_ind].copy()
            else:
                t0 = time.time()
                # do reference calculation
                new_config.calc = ref_calc

                data_keys = fit_kwargs['data_keys']
                if 'E' in data_keys:
                    try:
                        E = new_config.get_potential_energy(force_consistent=True)
                    except PropertyNotImplementedError:
                        warnings.warn(f"No force_consistent=True energy found for new config with reference calculator {ref_calc}")
                        E = new_config.get_potential_energy()
                    new_config.info[data_keys['E']] = E
                if 'F' in data_keys:
                    F = new_config.get_forces()
                    new_config.new_array(data_keys['F'], F)
                if 'V' in data_keys:
                    try:
                        V = - new_config.get_volume() * new_config.get_stress(voigt=False)
                        new_config.info[data_keys['V']] = V
                    except PropertyNotImplementedError:
                        warnings.warn(f"No stress for new config with reference calculator {ref_calc}")
                print("TIMING reference_calc", time.time() - t0)


            # save new config to fit or test set
            rv = np.random.uniform()
            if rv > test_fraction:
                # fit config chosen
                new_fit_configs.append(new_config)
                new_config_file = file_root.parent / (file_root.name + f".config_fit.{HAL_label}.extxyz")

                if ref_calc is not None:
                    # cause a refit below, whether or not basis is re-optimized
                    committee_calc = None
            else:
                # test config chosen, not need for refit
                new_test_configs.append(new_config)
                new_config_file = file_root.parent / (file_root.name + f".config_test.{HAL_label}.extxyz")

            ase.io.write(new_config_file, new_config)
        else:
            print("skipping reference calculation becuase HAL_iter is {}".format(iter_HAL))

        #reoptimise basis if HAL_step == 0
        if (basis_optim_kwargs is not None and basis_optim_interval is not None and
            iter_HAL % basis_optim_interval == basis_optim_interval - 1):
            # rows = len(fit_configs + new_fit_configs)* 7 + 3*sum([len(s) for s in fit_configs + new_fit_configs])
            # print("jpd47 rows is {}".format(rows))
            # basis_optim_kwargs["max_basis_len"] = rows
            t0 = time.time()
            # optimize basis

            #modify this to pass the current basis as well -only check degrees either side of this one.
            basis_info = _optimize_basis(fit_configs + new_fit_configs, basis_source, solver, fit_kwargs,
                                         basis_optim_kwargs)
            print("HAL got optimized basis", basis_info)

            B_len_norm = define_basis(basis_info, basis_source)
            # reset calculator to trigger a it with the new basis based on the optimized basis_info
            committee_calc = None
            print("TIMING basis_optim", time.time() - t0)

        if committee_calc is None:
            t0 = time.time()
            # re-fit (whether because of new config or new basis or both)
            # label potential with next iteration, since that's when it will be used

            #jpd47 apply broadening
            if atom_sigma > 0:
                print("overriding basis normalisation using gaussian broadening with atom_sigma={}".format(atom_sigma))
                r_nn = 2.5
                r_cut = 5.5
                a_n = (atom_sigma/r_cut)**2
                a_l = (atom_sigma/(5*r_nn))**2
                znl = B_len_norm[-1]
                gauss_norm = np.array([np.exp(a_n*sum([n**2 for n in d["ns"]]) + a_l*sum([l**2 for l in d["ls"]])) for d in znl])
                B_len_norm[2] = gauss_norm
                #solver.var_c_0=np.array(gauss_norm)**-1

            committee_calc = _fit(fit_configs + new_fit_configs, solver, fit_kwargs, B_len_norm, file_root, _HAL_label(iter_HAL + 1))
            error_configs = [("fit", fit_configs + new_fit_configs)]
            if len(test_configs + new_test_configs) > 0 or test_fraction > 0:
                error_configs.append(("test", test_configs + new_test_configs))
            print("FIT ERROR TABLE")
            print(viz.error_table(error_configs, committee_calc, fit_kwargs["data_keys"]))
            print("TIMING fit", time.time() - t0)

    # return fit configs, final basis_info, and optionally test configs
    if test_fraction > 0.0:
        return new_fit_configs, basis_info, new_test_configs
    else:
        return new_fit_configs, basis_info


def _optimize_basis(fit_configs, basis_source, solver, fit_kwargs, basis_optim_kwargs):
    """Optimize a basis

    Parameters
    ----------
    fit_configs: list(Atoms)
        atomic configurations to fit
    basis_source: str
        source for basis (module or julia source code)
    solver: LinearSolver
        solver for linear problem
    fit_kwargs: dict
        keyword args for fit()
    basis_optim_kwargs: dict
        keyword args for optimize_basis()

    Returns
    -------
    basis_info dict with parameters that can be passed to define_basis()
    """
    # do the optimization
    basis_info = optimize(solver=solver, fitting_db=fit_configs,
            basis_kwargs={"julia_source": basis_source},
            fit_kwargs=fit_kwargs, **basis_optim_kwargs)

    return basis_info


def _fit(fit_configs, solver, fit_kwargs, B_len_norm, file_root, HAL_label):
    """Do a fit

    Parameters
    ----------
    fit_configs: list(Atoms)
        fitting config
    solver: sklearn LinearSolver
        solver for linear problem
    fit_kwargs: dict
        other arguments for fit.fit()
    B_len_norm: tuple(basis, int, vector / None)
        basis, its length, and optional normalization vector returned by define_basis
    file_root: str / Path
        root part of path used to save potential
    HAL_label: str
        label added to file_root for filenames

    Returns
    -------
    committee_calc ACECommitteeCalc
    """
    # no calculator defined, fit one with the current fitting configs and basis
    pot_filename = str(file_root.parent / (file_root.name + f".pot.{HAL_label}.json"))

    committee_calc = fit(atoms_list=fit_configs, solver=solver, B_len_norm=B_len_norm,
                         return_linear_problem=False, pot_file=pot_filename, **fit_kwargs)

    plot_dimers_file = file_root.parent / (file_root.name + f".dimers.{HAL_label}.pdf")
    viz.plot_dimers(committee_calc, list(fit_kwargs["E0s"]), plot_dimers_file)

    return committee_calc
