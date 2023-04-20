import importlib
from ase.io import read
import multiprocessing
Ncores = multiprocessing.cpu_count()

#load Julia and Python dependencies
from julia.api import Julia
jl = Julia(compiled_modules=False)
from julia import Main

#set number of julia processes to use
from julia.Distributed import addprocs
addprocs(Ncores)

Main.eval("using ASE, JuLIP, ACE1, ACE1pack, ACE1x, LinearAlgebra")
#Main.eval("using ACE1x")

def get_Psi(dataset, basis_info, data_keys):
    #Main.basis_info = basis_info
    #Main.elements = list(basis_info["E0s"].keys())
    Main.eval("elements=[:Fe, :Ni, :Cr]")
    Main.cor_order = basis_info["cor_order"]
    Main.maxdeg = basis_info["maxdeg"]
    Main.r_cut = basis_info["r_cut"]
    Main.radial_transform = basis_info["radial_transform"]
    Main.Eref = basis_info["E0s"]
    Main.energy_key = data_keys["E"]
    Main.force_key = data_keys["F"]
    Main.virial_key = data_keys["V"]
    Main.weights = {"default": {"E":1.0, "F": 1.0, "V":1.0}}
    #create model
    Main.eval('model = ACE1x.acemodel(elements = elements, order = cor_order, totaldegree = maxdeg, rcut = r_cut, Eref = Eref)')

    #read in the atoms object from temporary file
    Main.eval('rawdata = read_extxyz("/data/jpd47/FeNiCr/Lakshmi_orig/data_test.xyz"')

    #convert to data
    Main.eval("""
    data =[ AtomsData(at, energy_key=energy_key, force_key=force_key, virial_key=virial_key, weights=weights,  v_ref=model.Vref) for at in rawdata]
    """)

    #linear assemble
    Main.eval("A, Y, W = ACEfit.linear_assemble(data, model.basis, :distributed)")


dataset = read("/data/jpd47/FeNiCr/Lakshmi_orig/data_test.xyz", index=":")
E0s = { "Fe" : 1.0, "Ni" : 1.0, "Cr" : 1.0 }
fixed_basis_info = {"E0s":E0s, "smoothness_prior" : None,  "r_cut": 5.5, "maxdeg": 5, "cor_order":2, "radial_transform":None}
data_keys = { "E" : "energy", "F" : "forces", "V" : "virial", "Fmax" : 10.0 }
Psi = get_Psi(dataset, fixed_basis_info, data_keys)