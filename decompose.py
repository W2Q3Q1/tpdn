from __future__ import unicode_literals, print_function, division
from io import open
import unicodedata
import string
import re
import random
from random import shuffle

import torch
import torch.nn as nn
from torch.autograd import Variable
from torch import optim
import torch.nn.functional as F

import sys
import os

import time
import math

import pickle

import argparse

from tasks import *
from training import *
from models import *
from evaluation import *
from role_assignment_functions import *

import numpy as np

# Code for performing a tensor product decomposition on an
# existing set of vectors

use_cuda = torch.cuda.is_available()

parser = argparse.ArgumentParser()
parser.add_argument("--data_prefix", help="prefix for the vectors", type=str, default=None)
parser.add_argument("--role_prefix", help="prefix for a file of roles (if used)", type=str, default=None)
parser.add_argument("--role_scheme", help="pre-coded role scheme to use", type=str, default=None)
parser.add_argument("--test_decoder", help="whether to test the decoder (in addition to MSE", type=str, default="False")
parser.add_argument("--decoder", help="decoder type", type=str, default="ltr")
parser.add_argument("--decoder_prefix", help="prefix for the decoder to test", type=str, default=None)
parser.add_argument("--decoder_embedding_size", help="embedding size for decoder", type=int, default=20)
parser.add_argument("--decoder_task", help="task performed by the decoder", type=str, default="auto")
parser.add_argument("--filler_dim", help="embedding dimension for fillers", type=int, default=10)
parser.add_argument("--role_dim", help="embedding dimension for roles", type=int, default=6)
parser.add_argument("--vocab_size", help="vocab size for the training language", type=int, default=10)
parser.add_argument("--hidden_size", help="size of the encodings", type=int, default=60)
parser.add_argument("--save_vectors", help="whether to save vectors generated by the fitted TPR model", type=str, default="False")
parser.add_argument("--save_role_dicts", help="whether to save role_to_index and index_to_role or not", type=str, default="False")
parser.add_argument("--shuffle", help="whether to use shuffling as a baseline", type=str, default="False")
parser.add_argument("--embedding_file", help="file containing pretrained embeddings", type=str, default=None)
parser.add_argument("--unseen_words", help="if using pretrained embeddings: whether to use all zeroes for unseen words' embeddings, or to give them random vectors", type=str, default="random")
parser.add_argument("--extra_test_set", help="additional file to print predictions for", type=str, default=None)
parser.add_argument("--train", help="whether or not to train the model", type=str, default="True")
parser.add_argument("--neighbor_analysis", help="whether to use a neighbor analysis", type=str, default="True")
parser.add_argument("--digits", help="whether this is one of the digit task", type=str, default="True")
parser.add_argument("--final_linear", help="whether to have a final linear layer", type=str, default="True")
parser.add_argument("--embed_squeeze", help="original dimension to be squeezed to filler_dim", type=int, default=None)
args = parser.parse_args()

# Create the logfile
if args.final_linear != "True":
	results_page = open("logs/" + args.data_prefix.split("/")[-1] + str(args.role_prefix).split("/")[-1] + str(args.role_scheme) + ".filler" + str(args.filler_dim) + ".role" + str(args.role_dim) + ".tpr_decomp.nf", "w")
else:
	results_page = open("logs/" + args.data_prefix.split("/")[-1] + str(args.role_prefix).split("/")[-1] + str(args.role_scheme) + ".filler" + str(args.filler_dim) + ".role" + str(args.role_dim) + "." + str(args.embed_squeeze) + ".tpr_decomp", "w")


# Load the decoder for computing swapping accuracy
if args.test_decoder == "True":
	if args.decoder == "ltr":
		decoder = DecoderRNN(args.vocab_size, args.decoder_embedding_size, args.hidden_size)
	elif args.decoder == "bi":
		decoder = DecoderBiRNN(args.vocab_size, args.decoder_embedding_size, args.hidden_size)
	elif args.decoder == "tree":
		decoder = DecoderTreeRNN(args.vocab_size, args.decoder_embedding_size, args.hidden_size)
	else:
		print("Invalid decoder type")

	input_to_output = lambda seq: transform(seq, args.decoder_task)

	decoder.load_state_dict(torch.load("models/decoder_" + args.decoder_prefix + ".weights"))

	if use_cuda:
		decoder = decoder.cuda()

# Prepare the train, dev, and test data
unindexed_train = []
unindexed_dev = []
unindexed_test = []
unindexed_extra = []

filler_to_index = {}
index_to_filler = {}
role_to_index = {}
index_to_role = {}

filler_counter = 0
role_counter = 0
max_length = 0

train_file = open("data/" + args.data_prefix + ".data_from_train", "r")
for line in train_file:
	sequence, vector = line.strip().split("\t")
	if use_cuda:
		unindexed_train.append(([value for value in sequence.split()], Variable(torch.FloatTensor(np.array([float(value) for value in vector.split()]))).cuda()))
	else:
		unindexed_train.append(([value for value in sequence.split()], Variable(torch.FloatTensor(np.array([float(value) for value in vector.split()])))))

	if len(sequence.split()) > max_length:
		max_length = len(sequence.split())

	for filler in sequence.split():
		if filler not in filler_to_index:
			filler_to_index[filler] = filler_counter
			index_to_filler[filler_counter] = filler
			filler_counter += 1
		

dev_file = open("data/" + args.data_prefix + ".data_from_dev", "r")
for line in dev_file:
	sequence, vector = line.strip().split("\t")
	if use_cuda:
		unindexed_dev.append(([value for value in sequence.split()], Variable(torch.FloatTensor(np.array([float(value) for value in vector.split()]))).cuda()))
	else:
		unindexed_dev.append(([value for value in sequence.split()], Variable(torch.FloatTensor(np.array([float(value) for value in vector.split()])))))

	if len(sequence.split()) > max_length:
		max_length = len(sequence.split())

	for filler in sequence.split():
		if filler not in filler_to_index:
			filler_to_index[filler] = filler_counter
			index_to_filler[filler_counter] = filler
			filler_counter += 1


test_file = open("data/" + args.data_prefix + ".data_from_test", "r")
for line in test_file:
	sequence, vector = line.strip().split("\t")

	if use_cuda:
		unindexed_test.append(([value for value in sequence.split()], Variable(torch.FloatTensor(np.array([float(value) for value in vector.split()]))).cuda()))
	else:
		unindexed_test.append(([value for value in sequence.split()], Variable(torch.FloatTensor(np.array([float(value) for value in vector.split()])))))

	if len(sequence.split()) > max_length:
		max_length = len(sequence.split())

	for filler in sequence.split():
		if filler not in filler_to_index:
			filler_to_index[filler] = filler_counter
			index_to_filler[filler_counter] = filler
			filler_counter += 1

if args.extra_test_set is not None:
	extra_file = open("data/" + args.extra_test_set, "r")
	for line in extra_file:
		sequence, vector = line.strip().split("\t")
		unindexed_extra.append(([value for value in sequence.split()], Variable(torch.FloatTensor(np.array([float(value) for value in vector.split()]))).cuda()))
	
		if len(sequence.split()) > max_length:
			max_length = len(sequence.split())

		for filler in sequence.split():
			if filler not in filler_to_index:
				filler_to_index[filler] = filler_counter
				index_to_filler[filler_counter] = filler
				filler_counter += 1

if args.digits == "True":
	for i in range(10):
		filler_to_index[str(i)] = i
		index_to_filler[i] = str(i)


indexed_train = [([filler_to_index[filler] for filler in elt[0]], elt[1]) for elt in unindexed_train]
indexed_dev = [([filler_to_index[filler] for filler in elt[0]], elt[1]) for elt in unindexed_dev]
indexed_test = [([filler_to_index[filler] for filler in elt[0]], elt[1]) for elt in unindexed_test]
indexed_extra = [([filler_to_index[filler] for filler in elt[0]], elt[1]) for elt in unindexed_extra]

unindexed_train_roles = []
unindexed_dev_roles = []
unindexed_test_roles = []
unindexed_extra_roles = []

n_r = -1

# If there is a file of roles for the fillers, load those roles
if args.role_prefix is not None:
	train_role_file = open("data/" + args.role_prefix + ".data_from_train.roles", "r")
	for line in train_role_file:
		unindexed_train_roles.append(line.strip().split())
		for role in line.strip().split():
			if role not in role_to_index:
				role_to_index[role] = role_counter
				index_to_role[role_counter] = role
				role_counter += 1

	dev_role_file = open("data/" + args.role_prefix + ".data_from_dev.roles", "r")
	for line in dev_role_file:
		unindexed_dev_roles.append(line.strip().split())
		for role in line.strip().split():
			if role not in role_to_index:
				role_to_index[role] = role_counter
				index_to_role[role_counter] = role
				role_counter += 1

	test_role_file = open("data/" + args.role_prefix + ".data_from_test.roles", "r")
	for line in test_role_file:
		unindexed_test_roles.append(line.strip().split())
		for role in line.strip().split():
			if role not in role_to_index:
				role_to_index[role] = role_counter
				index_to_role[role_counter] = role
				role_counter += 1

	if args.extra_test_set is not None:
		extra_role_file = open("data/" + args.extra_test_set + ".roles", "r")
		for line in extra_role_file:
			unindexed_extra_roles.append(line.strip().split())
			for role in line.strip().split():
				if role not in role_to_index:
					role_to_index[role] = role_counter
					index_to_role[role_counter] = role
					role_counter += 1



# Or, if a predefined role scheme is being used, prepare it
elif args.role_scheme is not None:
	if args.role_scheme == "bow":
		n_r, seq_to_roles = create_bow_roles(max_length, len(filler_to_index.keys()))
	elif args.role_scheme == "ltr":
		n_r, seq_to_roles = create_ltr_roles(max_length, len(filler_to_index.keys()))
	elif args.role_scheme == "rtl":
		n_r, seq_to_roles = create_rtl_roles(max_length, len(filler_to_index.keys()))
	elif args.role_scheme == "bi":
		n_r, seq_to_roles = create_bidirectional_roles(max_length, len(filler_to_index.keys()))
	elif args.role_scheme == "wickel":
		n_r, seq_to_roles = create_wickel_roles(max_length, len(filler_to_index.keys()))
	elif args.role_scheme == "tree":
		n_r, seq_to_roles = create_tree_roles(max_length, len(filler_to_index.keys()))
	else:
		print("Invalid role scheme")

	for pair in indexed_train:
		these_roles = seq_to_roles(pair[0])
		unindexed_train_roles.append(these_roles)
		for role in these_roles:
			if role not in role_to_index:
				role_to_index[role] = role
				index_to_role[role] = role
				role_counter += 1

	for pair in indexed_dev:
		these_roles = seq_to_roles(pair[0])
		unindexed_dev_roles.append(these_roles)
		for role in these_roles:
			if role not in role_to_index:
				role_to_index[role] = role
				index_to_role[role] = role
				role_counter += 1


	for pair in indexed_test:
		these_roles = seq_to_roles(pair[0])
		unindexed_test_roles.append(these_roles)
		for role in these_roles:
			if role not in role_to_index:
				role_to_index[role] = role
				index_to_role[role] = role
				role_counter += 1


	for pair in indexed_extra:
		these_roles = seq_to_roles(pair[0])
		unindexed_extra_roles.append(these_roles)
		for role in these_roles:
			if role not in role_to_index:
				role_to_index[role] = role
				index_to_role[role] = role
				role_counter += 1
else:
	print("No role scheme specified")


indexed_train_roles = [[role_to_index[role] for role in roles] for roles in unindexed_train_roles]
indexed_dev_roles = [[role_to_index[role] for role in roles] for roles in unindexed_dev_roles]
indexed_test_roles = [[role_to_index[role] for role in roles] for roles in unindexed_test_roles]
indexed_extra_roles = [[role_to_index[role] for role in roles] for roles in unindexed_extra_roles]


all_train_data = []
all_dev_data = []
all_test_data = []
all_extra_data = []


# Make sure the number of fillers and the number of roles always matches
for index, element in enumerate(indexed_train):
	if len(element[0]) != len(indexed_train_roles[index]):
		print(index, "ERROR!!!", element[0], indexed_train_roles[index])
	else:
		all_train_data.append((element[0], indexed_train_roles[index], element[1]))

for index, element in enumerate(indexed_dev):
	if len(element[0]) != len(indexed_dev_roles[index]):
		print(index, "ERROR!!!", element[0], indexed_dev_roles[index])
	else:
		all_dev_data.append((element[0], indexed_dev_roles[index], element[1]))

for index, element in enumerate(indexed_test):
	if len(element[0]) != len(indexed_test_roles[index]):
		print(index, "ERROR!!!", element[0], indexed_test_roles[index])
	else:
		all_test_data.append((element[0], indexed_test_roles[index], element[1]))


for index, element in enumerate(indexed_extra):
	if len(element[0]) != len(indexed_extra_roles[index]):
		print(index, "ERROR!!!", element[0], indexed_extra_roles[index])
	else:
		all_extra_data.append((element[0], indexed_extra_roles[index], element[1]))


weights_matrix = None

# Prepare the embeddings
# If a file of embeddings was provided, use those.
embedding_dict = None
if args.embedding_file is not None:
	embedding_dict = {}
	embed_file = open(args.embedding_file, "r")
	for line in embed_file:
		parts = line.strip().split()
		if len(parts) == args.filler_dim + 1:
			embedding_dict[parts[0]] = list(map(lambda x: float(x), parts[1:]))

	matrix_len = len(filler_to_index.keys())
	if args.embed_squeeze is not None:
		weights_matrix = np.zeros((matrix_len, args.embed_squeeze))
	else:
		weights_matrix = np.zeros((matrix_len, args.filler_dim))

	for i in range(matrix_len):
		word = index_to_filler[i]
		if word in embedding_dict:
			weights_matrix[i] = embedding_dict[word]
		else:
			if args.unseen_words == "random":
				weights_matrix[i] = np.random.normal(scale=0.6, size=(args.filler_dim,))
			elif args.unseen_words == "zero":
				pass # It was initialized as zero, so don't need to do anything
			else:
				print("Invalid choice for embeddings of unseen words")

# Initialize the TPDN
if n_r != -1:
	role_counter = n_r

if args.final_linear == "True":
	tpr_encoder = TensorProductEncoder(n_roles=role_counter, n_fillers=filler_counter, final_layer_width=args.hidden_size, 
					filler_dim=args.filler_dim, role_dim=args.role_dim, pretrained_embeddings=weights_matrix, embedder_squeeze=args.embed_squeeze)
else:
	tpr_encoder = TensorProductEncoder(n_roles=role_counter, n_fillers=filler_counter, final_layer_width=None,
					filler_dim=args.filler_dim, role_dim=args.role_dim, pretrained_embeddings=weights_matrix, embedder_squeeze=args.embed_squeeze) 

if use_cuda:
	tpr_encoder = tpr_encoder.cuda()



args.data_prefix = args.data_prefix.split("/")[-1] + ".filler" + str(args.filler_dim) + ".role" + str(args.role_dim)
if args.final_linear != "True":
	args.data_prefix += ".no_final"

# Train the TPDN
args.role_prefix = str(args.role_prefix).split("/")[-1]
if args.train == "True":
	end_loss = trainIters_tpr(all_train_data, all_dev_data, tpr_encoder, 100, print_every=1000//32, learning_rate = 0.001, weight_file="models/" + args.data_prefix + str(args.role_prefix) + str(args.role_scheme) + ".tpr", batch_size=32)

# Load the trained TPDn
tpr_encoder.load_state_dict(torch.load("models/" + args.data_prefix + str(args.role_prefix) + str(args.role_scheme) + ".tpr"))

total_mse = 0

# Prepare test data
all_test_data_orig = all_test_data
all_test_data = batchify_tpr(all_test_data, 1)

if use_cuda:
    test_data_sets = [(Variable(torch.LongTensor([item[0] for item in batch])).cuda(),
                       Variable(torch.LongTensor([item[1] for item in batch])).cuda(),
                       torch.cat([item[2].unsqueeze(0).unsqueeze(0) for item in batch], 1)) for batch in all_test_data]
else:
    test_data_sets = [(Variable(torch.LongTensor([item[0] for item in batch])),
                       Variable(torch.LongTensor([item[1] for item in batch])),
                       torch.cat([item[2].unsqueeze(0).unsqueeze(0) for item in batch], 1)) for batch in all_test_data]


neighbor_counter = 0
neighbor_total_rank = 0
neighbor_correct = 0

def distance(vec_a, vec_b):
	return np.average(np.square(vec_a - vec_b))

all_vecs = [elt[0][2].unsqueeze(0).cpu().numpy() for elt in all_test_data] + [elt[2].unsqueeze(0).cpu().numpy() for elt in all_train_data] + [elt[2].unsqueeze(0).cpu().numpy() for elt in all_dev_data]

all_vecs = np.concatenate(all_vecs, axis=0)

# Evaluate on test set
for i in range(len(all_test_data)): 
	encoding = tpr_encoder(test_data_sets[i][0], test_data_sets[i][1])

	total_mse += torch.mean(torch.pow(encoding.data - test_data_sets[i][2].data, 2))




final_test_loss = total_mse / len(all_test_data) 

results_page.write(args.data_prefix + str(args.role_prefix) + str(args.role_scheme) + ".tpr" +  " MSE on test set: " + str( final_test_loss.item()) + "\n" )

if args.test_decoder == "True":
	correct, total = score2(tpr_encoder, decoder, input_to_output, batchify(all_test_data, 1), index_to_filler)
	results_page.write(args.data_prefix + str(args.role_prefix) + str(args.role_scheme) + ".tpr" + " Swapping encoder performance: " + str(correct) + " " +  str(total) + "\n")



# Save the test set predictions, if desired
if args.save_vectors == "True":
	fo_pred = open(args.data_prefix + str(args.role_prefix) + str(args.role_scheme) + ".tpr.test_preds", "w")


	for i in range(len(test_data_sets)):
		sequence = all_test_data[i][0]
		pred = tpr_encoder(test_data_sets[i][0], test_data_sets[i][1]).data.cpu().numpy()[0][0]
		
		sequence = [str(x) for x in sequence]
		pred = [str(x) for x in pred]
	
		fo_pred.write(" ".join(sequence) + "\t" + " ".join(pred) + "\n")




# Save the role dictionaries, if desired
if args.save_role_dicts == "True":
	with open(args.data_prefix + str(args.role_prefix) + str(args.role_scheme) + '.role_to_index.pickle', 'wb') as handle:
		pickle.dump(role_to_index, handle, protocol=pickle.HIGHEST_PROTOCOL)
		
	with open(args.data_prefix + str(args.role_prefix) + str(args.role_scheme) + '.index_to_role.pickle', 'wb') as handle:
		pickle.dump(index_to_role, handle, protocol=pickle.HIGHEST_PROTOCOL)


