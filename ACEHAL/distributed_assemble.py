import os
Ncores = int(os.getenv("ASSEMBLE_PROCS"))
print("using {} cores for assembling Psi".format(Ncores))

import importlib
from ase.io import read, write
from basis import define_basis

#load Julia and Python dependencies
from julia.api import Julia
jl = Julia(compiled_modules=False)
from julia import Main

#set number of julia processes to use
from julia.Distributed import addprocs
addprocs(Ncores)

#Main.eval("using ASE, JuLIP, ACE1, ACE1pack, ACE1x, LinearAlgebra")
#Main.eval("using ACE1x")
Main.eval("using ACE1pack")

def get_Psi(dataset, B, data_keys):
    """writes the dataset to be read in by julia to avoid missing data"""
    dataset_name = os.getcwd() + "/DA_temp.extxyz"
    print("dataset_name is", dataset_name)
    write(dataset_name, dataset)

    Main.dataset_name = dataset_name
    Main.energy_key = data_keys["E"]
    Main.force_key = data_keys["F"]
    Main.virial_key = data_keys["V"]
    Main.weights = {"default": {"E":1.0, "F": 1.0, "V":1.0}}
    Main.basis = B

    print("reading in data")
    Main.eval('rawdata = read_extxyz(dataset_name)')
    print("AtomsData")
    Main.eval("""
    data =[ AtomsData(at, energy_key=energy_key, force_key=force_key, virial_key=virial_key, weights=weights) for at in rawdata]
    """)
    print("linear_assemble")
    Main.eval("A, Y, W = ACEfit.linear_assemble(data, basis, :distributed)")

    #remove temporary file to tidy up
    os.remove(dataset_name)
    return Main.A, Main.Y, Main.W

# #Get the basis from basis.py
# E0s = { "Fe" : 1.0, "Ni" : 1.0, "Cr" : 1.0 }
# fixed_basis_info = {"elements": list(E0s.keys()), "smoothness_prior" : None,  "r_cut": 5.5, "maxdeg": 14, "cor_order":2, "radial_transform":None}
# B_len_norm_znl = define_basis(fixed_basis_info)
# for x in B_len_norm_znl:
#     print(type(x))

# data_keys = { "E" : "energy", "F" : "forces", "V" : "virial", "Fmax" : 10.0 }
# #dataset = read("/data/jpd47/FeNiCr/Lakshmi_orig/data_test.xyz", index=":")
# dataset_name = "/data/jpd47/FeNiCr/Lakshmi_orig/data_test.xyz"
# Psi, Y, W = get_Psi(dataset_name, B_len_norm_znl[0], data_keys)
# print(type(Psi), type(Y), type(W))
# print(Psi.shape, Y.shape, W.shape)
# print(Psi[:,0])


# # fixed_basis_info = {"E0s":E0s, "smoothness_prior" : None,  "r_cut": 5.5, "maxdeg": 5, "cor_order":2, "radial_transform":None}
# #
# # Psi = get_Psi(dataset, fixed_basis_info, data_keys)