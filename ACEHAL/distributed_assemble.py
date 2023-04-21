import os
Ncores = int(os.getenv("ASSEMBLE_PROCS"))
print("using {} cores for assembling Psi".format(Ncores))

import importlib
from ase.io import read, write

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

def get_Psi(dataset, B, data_keys, weights):
    """writes the dataset to be read in by julia to avoid missing data"""
    dataset_name = os.getcwd() + "/DA_temp.extxyz"
    print("dataset_name is", dataset_name)
    write(dataset_name, dataset)

    Main.dataset_name = dataset_name
    Main.energy_key = data_keys["E"]
    Main.force_key = data_keys["F"]
    Main.virial_key = data_keys["V"]
    Main.weights = {"default": {"E":1.0, "F": 1.0, "V": 1.0}}
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

    Psi = Main.A
    Y = Main.Y
    Psi_w, Y_w = apply_weights(Psi_dist, Y_dist, dataset, weights)
    return Psi_w, Y_w

def apply_weights(Psi_dist, Y_dist, configs, weights):
    #shuffle this into distributed_assemble
    i = 0
    for at in configs:
        n = len(at)

        #energy
        Psi_dist[i, :] *= weights["E_per_atom"]/n
        Y_dist[i] *= weights["E_per_atom"]/n
        i += 1

        #forces
        Psi_dist[i:i+3*n,:] *= weights["F"]
        Y_dist[i:i+3*n] *= weights["F"]
        i += 3*n

        #virials
        Psi_dist[i:i+6, :] *= weights["V_per_atom"]/n
        Y_dist[i:i+6] *= weights["V_per_atom"]/n
        i += 6
    return Psi_dist, Y_dist
