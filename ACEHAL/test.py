
import time
import numpy as np
t1 = time.time()
from basis import define_basis
from distributed_assemble import get_Psi
t2 = time.time()
print("finished imports in {}".format(t2-t1))

from ase.io import read, write
dataset_name = "/data/jpd47/FeNiCr/Lakshmi_orig/data_test.xyz"
dataset = read(dataset_name, index=":")

#Get the basis from basis.py
E0s = { "Fe" : 1.0, "Ni" : 1.0, "Cr" : 1.0 }
fixed_basis_info = {"elements": list(E0s.keys()), "smoothness_prior" : None,  "r_cut": 5.5, "maxdeg": 6, "cor_order":2, "radial_transform":None}
B_len_norm_znl = define_basis(fixed_basis_info)
for x in B_len_norm_znl:
    print(type(x))

data_keys = { "E" : "energy", "F" : "forces", "V" : "virial", "Fmax" : 10.0 }

Psi, Y, W = get_Psi(dataset, B_len_norm_znl[0], data_keys)
print(type(Psi), type(Y), type(W))
print(Psi.shape, Y.shape, W.shape)
np.save("Psi_2-6", Psi)
np.save("Y_2-6", Y)
np.save("W_2-6", W)

fixed_basis_info = {"elements": list(E0s.keys()), "smoothness_prior" : None,  "r_cut": 5.5, "maxdeg": 14, "cor_order":2, "radial_transform":None}
B_len_norm_znl = define_basis(fixed_basis_info)
for x in B_len_norm_znl:
    print(type(x))

Psi, Y, W = get_Psi(dataset, B_len_norm_znl[0], data_keys)
print(type(Psi), type(Y), type(W))
print(Psi.shape, Y.shape, W.shape)
np.save("Psi_2-14", Psi)
np.save("Y_2-14", Y)
np.save("W_2-14", W)
