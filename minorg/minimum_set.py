import os
import re
import sys
import itertools
from Bio.Seq import Seq

from functions import (
    splitlines,
    fasta_to_dict, dict_to_fasta,
    gRNAHits
)

# sys.path.append("/mnt/chaelab/rachelle/src")
# from data_manip import splitlines
# from fasta_manip import fasta_to_dict, dict_to_fasta
# sys.path.append(os.path.realpath(__file__))
# from find_gRNA_functions import *

impossible_set_message_default = "Consider adjusting --minlen, --minid, and --merge-within, and/or raising --check-reciprocal to restrict candidate target sequences to true biological replicates. Also consider using --domain to restrict the search."

## get_minimum_sets_and_write, except instead of accepting gRNA_dict it accepts 'mapping' file and parses that into a gRNAHits object
def get_minimum_sets_from_files_and_write(mapping, targets = None, input_map_ver = None, **kwargs):
    ## read data
    gRNA_hits = gRNAHits()
    gRNA_hits.parse_from_mapping(mapping, targets = targets, version = input_map_ver)
    return get_minimum_sets_and_write(gRNA_hits, target_len_provided = gRNA_hits.all_target_len_valid(),
                                      **kwargs)

## TODO: create gRNAHits object to store what is currently most frequently called gRNA_dict
## this function gets {num_sets} number of minimum sets and writes the final sets to files
def get_minimum_sets_and_write(gRNA_hits, num_sets = 1, targets = None, exclude_targets = set(),
                               fasta = None, prefix = None, directory = None,
                               fout_fasta = None, fout_mapping = None, exclude_fname = None,
                               accept_unknown_within_feature_status = False, exclude_seqs = set(),
                               ignore_invalid = True, output_map_ver = 3, **kwargs):
    
    ## assume all targets in gRNA_hits are to be, well, targeted
    targets = ( targets if targets is not None else \
                set(hit.target_id() for hit in gRNA_hits.flatten_hits() \
                    if hit.target_id() not in exclude_targets) )
    
    ## remove sequences not included in fasta file from gRNA_hits if fasta file provided
    if fasta:
        gRNA_hits.remove_seqs(*[seq for seq in gRNA_hits.seqs() if
                                str(seq).upper() not in map(lambda x: str(x).upper(),
                                                            fasta_to_dict(fasta).values())])
        gRNA_hits.rename_seqs(fasta)
    
    ## remove sequences in exclude_fname file from gRNA_hits if file provided
    if exclude_fname: filter_excluded_seqs(gRNA_hits, exclude_fname)
    ## check if within feature status has been set. If not, warn user.
    print("Checking if within feature check has been set")
    gRNA_feature_status_set = set( hit.check("feature") for hit in gRNA_hits.flatten_hits() )
    if ( None in gRNA_feature_status_set ): ## if feature status has not been set for at >= 1 gRNAHit object
        print("\nWARNING: The within-feature status of at least one gRNA hit is not known.")
        if accept_unknown_within_feature_status:
            print("The programme will assume that these hits are NOT within coding regions and remove them from the list of viable candidates.\n")
        else:
            print("The programme will assume that these hits are within coding regions and treat them as viable candidates.\n")
    ## filter feature and remaining valid checks
    print("Filtering gRNA by checks")
    filtered_gRNA_hits = gRNA_hits.filter_hits_some_checks_passed("feature", ignore_invalid = ignore_invalid,
                                                                  exclude_empty_seqs = True)
    filtered_gRNA_hits = filtered_gRNA_hits.filter_seqs_all_checks_passed(ignore_invalid = True)
    if len(filtered_gRNA_hits) < num_sets:
        print(f"\nWARNING: The gRNA sequences cannot cover all target sequences the desired number of times ({len(filtered_gRNA_hits)} valid gRNA, {num_sets} set(s) requested).\n")
        return
    ## start generating sets
    print(f"Generating gRNA sets from {len(filtered_gRNA_hits)} possible gRNA")
    gRNA_sets = []
    while len(gRNA_sets) < num_sets:
        ## get a (minimum) set of gRNA sequences
        seq_set = get_minimum_set(filtered_gRNA_hits,
                                  set_num = len(gRNA_sets) + 1,
                                  targets = targets, **kwargs)
        ## if valid set returned
        if seq_set:
            gRNA_sets.append(seq_set) ## add to existing list of sets
            filtered_gRNA_hits.remove_seqs(seq_set) ## remove seqs in seq_set so they're not repeated
        else:
            print(f"\nWARNING: The gRNA sequences cannot cover all target sequences the desired number of times ({num_sets}). (Failed at set {len(gRNA_sets) + 1} of {num_sets})\n")
            break
    ## fout locations
    if not fout_fasta: fout_fasta = f"{directory}/{prefix}.fasta"
    if not fout_mapping: fout_mapping = f"{directory}/{prefix}_targets.txt"
    ## write gRNA fasta file and gRNA-target mapping
    if gRNA_sets:
        gRNA_hits.write_fasta(fout_fasta, seqs = itertools.chain(*gRNA_sets), fasta = fasta)
        print(f"Final gRNA sequence(s) have been written to {fout_fasta}")
        gRNA_hits.write_mapping(fout_mapping, sets = gRNA_sets, fasta = fasta, version = output_map_ver)
        print(f"Final gRNA sequence ID(s), gRNA sequence(s), and target(s) have been written to {fout_mapping}")
    ## print summary
    print(f"\n{num_sets} mutually exclusive gRNA set(s) requested. {len(gRNA_sets)} set(s) found.")
    return

## gets a single minimum set
def get_minimum_set(gRNA_hits, manual_check = True, exclude_seqs = set(), targets = None,
                    target_len_provided = False, sc_algorithm = "LAR", set_num = 1,
                    impossible_set_message = impossible_set_message_default):
    while True:
        ## solve set_cover
        ## note: If antisense, tie break by minimum -end. Else, tie break by minimum start.
        ## note: tie-breaker uses AVERAGE distance of hits (to inferred N-terminus)
        seq_set = set_cover(gRNA_hits, (targets if targets is not None else
                                        set(hit.target_id() for hit in gRNA_hits.flatten_hits())),
                            algorithm = sc_algorithm, exclude_seqs = exclude_seqs,
                            id_key = lambda x: x.target_id(),
                            tie_breaker = lambda x: min(x.items(),
                                                        key = lambda y: sum((-z.range()[1] if
                                                                             z.target().sense() == '-' else
                                                                             z.range()[0])
                                                                            for z in y[1])/len(y[1])))
        ## if empty set, print message and break out of loop to exit and return the empty set
        if set(seq_set) == set():
            print(impossible_set_message)
            break
        ## if valid set AND manual check NOT requested, break out of loop to exit and return the valid set
        elif not manual_check: break
        ## if valid set AND manual check requested
        else:
            ## print gRNA sequences in seq_set to screen for user to evaluate
            gRNA_seq_set = sorted(gRNA_hits.get_gRNAseqs_by_seq(*seq_set), key = lambda gRNA_seq: gRNA_seq.id())
            print(f"\tID\tsequence (Set {set_num})")
            for gRNA_seq in gRNA_seq_set:
                print(f"\t{gRNA_seq.id()}\t{gRNA_seq.seq()}")
            ## obtain user input
            usr_input = input(f"Hit 'x' to continue if you are satisfied with these sequences. Otherwise, enter the sequence ID or sequence of an undesirable gRNA (case-sensitive) and hit the return key to update this list: ")
            if usr_input.upper() == 'X':
                break
            else:
                id_seq_dict = {gRNA_seq.id(): gRNA_seq.seq() for gRNA_seq in gRNA_seq_set}
                if usr_input in id_seq_dict:
                    exclude_seqs |= {str(id_seq_dict[usr_input])}
                elif usr_input.upper() in set(str(x).upper() for x in id_seq_dict.values()):
                    exclude_seqs |= {usr_input}
                else:
                    print("Invalid input.")
    return seq_set


##################
##  SET COVER   ##
##  ALGORITHMS  ##
##################

## note that tie_breaker function should work on dictionaries of {gRNA_seq: {gRNAHit objects}} and return a tuple or list of two values: (gRNA_seq, {gRNAHit objects})
def set_cover(gRNA_hits, target_ids, algorithm = "LAR", id_key = lambda x: x, **kwargs):
    exclude_seqs = set(map(lambda s: str(s).upper(), kwargs["exclude_seqs"]))
    gRNA_coverage = {seq: hits for seq, hits in gRNA_hits.hits().items()
                     if str(seq).upper() not in exclude_seqs}
    ## check if set cover is possible before attempting to solve set cover
    if ( set(id_key(y) for x in gRNA_coverage.values() for y in x) & set(target_ids) ) < set(target_ids):
        print("\nWARNING: The provided gRNA sequences cannot cover all target sequences.\n")
    elif algorithm == "LAR":
        return set_cover_LAR(gRNA_coverage, target_ids, id_key = id_key,
                             **{k: v for k, v in kwargs.items() if k in ["tie_breaker"]})
    elif algorithm == "greedy":
        return set_cover_greedy(gRNA_coverage, target_ids, id_key = id_key,
                                **{k: v for k, v in kwargs.items() if k in ["tie_breaker"]})
    return []

## LAR algorithm
def set_cover_LAR(gRNA_coverage, target_ids, id_key = lambda x: x, tie_breaker = lambda x: tuple(x.items())[0]):
    
    main_sorting_key = lambda k, uncovered_count: len(set(id_key(x) for x in uncovered_count[k]))
    result_cover, covered = {}, set()
    from copy import deepcopy
    uncovered_count = deepcopy(gRNA_coverage)
    ## get set cover
    while {len(v) for v in uncovered_count.values()} != {0}:
        sorting_key = lambda k: main_sorting_key(k, uncovered_count)
        max_val = sorting_key(max(uncovered_count, key = sorting_key))
        max_items = {seq: hits for seq, hits in uncovered_count.items()
                     if sorting_key(seq) == max_val}
        seq, coverage = tie_breaker(max_items)
        coverage = set(id_key(target) for target in coverage)
        covered |= coverage
        result_cover[seq] = coverage
        ## update coverage of unchosen seqs
        uncovered_count = {seq: set(hit for hit in hits if id_key(hit) not in covered)
                           for seq, hits in uncovered_count.items()}
    ## remove redundant sequences
    for seq_id, coverage in result_cover.items():
        coverage_remaining = set().union(*[v for k, v in result_cover.items() if k != seq_id])
        if coverage.issubset(coverage_remaining):
            del result_cover[seq_id]
    return set(result_cover.keys())


## greedy algorithm
def set_cover_greedy(gRNA_coverage, target_ids, id_key = lambda x: x, tie_breaker = lambda x: x.items()[0]):
    
    main_sorting_key = lambda k, covered: len(set(id_key(x) for x in gRNA_coverage[k]) - set(covered))
    coverage_set = lambda k: set(id_key(x) for x in gRNA_coverage[k])
    covered, desired = set(), []
    while set(covered) != set(target_ids):
        sorting_key = lambda k: main_sorting_key(k, covered)
        max_val = sorting_key(max(gRNA_coverage, key = sorting_key))
        max_items = {k: v for k, v in gRNA_coverage.items() if sorting_key(k) == max_val}
        subset = tie_breaker(max_items)[0]
        covered |= coverage_set(subset)
        desired.append(subset)
    return set(desired)
