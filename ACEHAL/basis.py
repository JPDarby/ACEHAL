import importlib

#load Julia and Python dependencies
from julia.api import Julia
jl = Julia(compiled_modules=False)
from julia import Main

_default_mod = "ACEHAL.bases.default"

def define_basis(basis_info, julia_source=None):
    f"""define an ACE basis using julia

    Runs julia source code that defines B, len_B and P_diag julia variables containing
    the basis, its length, and an optional normalization vector.  Parameters
    will be passed into julia as a dict named "basis_info".  Julia code must define
    "B" for the basis, "B_len" for its length, and "P_diag" vector of the same
    lenth for an optional basis normalization.

    Parameters
    ----------
    basis_info: dict
        parameters that are used by the julia source to construct the basis
    julia_source: str, default "{_default_mod}"
        Name of julia module defining string "source" with julia source
        and "params" list with required basis_info dict keys, or julia
        source code that defines the required symbols.

    Returns
    -------
    B: julia basis
    B_length int: length of basis
    normalization: numpy array(n_basis) or None normalization needed, e.g. to enforce smoothness prior
    """
    print("basis_info is", basis_info)
    if julia_source is None:
        julia_source = _default_mod

    try:
        basis_mod = importlib.import_module(julia_source)
        julia_source = basis_mod.source
        req_params = set(basis_mod.params)
        if len(req_params - set(basis_info.keys())) > 0:
            raise ValueError(f"Trying to construct julia basis from {basis_mod} "
                             f"with missing required params {req_params - set(basis_info)} and extra params {set(basis_info) - req_params}")
    except ModuleNotFoundError:
        pass

    Main.basis_info = basis_info

    Main.eval(julia_source)


    #jpd47 return an array
    #pair basis, Nones for species atm
    znl_data = []
    if type(basis_info["maxdeg"]) == int:
        Nmax = basis_info["maxdeg"]
    elif type(basis_info["maxdeg"]) == dict:
        Nmax = basis_info["maxdeg"][1]
    for i, Zi in enumerate(Main.ace_Zs):
        for Z2 in Main.ace_Zs[i:]:
            for n in range(1, Nmax+1):
                znl_data.append({"z0":None, "zs":None, "ns":[n], "ls":[0], "nu":1})
    #add main ace basis data
    Nz = len(Main.ace_Zs)
    N = len(Main.znl_data)//Nz
    for i, bb in enumerate(Main.znl_data):
        bdic = {"z0":Main.ace_Zs[i//N], "zs":[], "ns":[], "ls":[], "nu":len(bb)}
        for b in bb:
            bdic["zs"].append(b[0])
            bdic["ns"].append(b[1])
            bdic["ls"].append(b[2])
        znl_data.append(bdic)

    return Main.B, Main.B_length, Main.P_diag, znl_data
