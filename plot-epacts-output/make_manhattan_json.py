#!/usr/bin/env python2

# TODO: try csv.reader instead of .split('\t')
# TODO: use a single-pass instead of two passes.
#    - iterate through all variants, building the heap and putting all variants into bins
#    - at the end, remove the worst pval from the heap and save its pval as THRESHOLD
#    - then remove all pvals smaller than THRESHOLD from the bins.
#        - delete points
#        - shorten intervals

'''
This script takes two arguments:
- an input filename (the output of epacts with a single phenotype)
- an output filename (please end it with `.json`)

It creates a json file which can be used to render a Manhattan plot.
'''

from __future__ import print_function, division, absolute_import

import os.path
import sys
import gzip
import re
import json
import math
import collections
import heapq

BIN_LENGTH = int(3e6)
NEGLOG10_PVAL_BIN_SIZE = 0.05 # Use 0.05, 0.1, 0.15, etc
NEGLOG10_PVAL_BIN_DIGITS = 2 # Then round to this many digits
BIN_THRESHOLD = 1e-4 # pvals less than this threshold don't get binned.
MAX_NUM_UNBINNED = 5000 # if there are too many points with pvals < BIN_THRESHOLD, the browser gets overwhelmed.  This limits the number of unbinned variants.

def round_sig(x, digits):
    return 0 if x==0 else round(x, digits-1-int(math.floor(math.log10(abs(x)))))
assert round_sig(0.00123, 2) == 0.0012
assert round_sig(1.59e-10, 2) == 1.6e-10


def get_binning_pval_threshold(variants):
    # If too many variants have p-values smaller than the BIN_THRESHOLD, we want to make the BIN_THRESHOLD stricter (lower).
    pvals = (v.pval for v in variants)
    # similar to: sorted(pvals)[MAX_NUM_UNBINNED+1]
    # Use +1 because the largest pval in this heap will get binned.
    largest_of_MAX_NUM_UNBINNED_smallest_pvals = heapq.nsmallest(MAX_NUM_UNBINNED+1, pvals)[-1]
    return min(BIN_THRESHOLD, largest_of_MAX_NUM_UNBINNED_smallest_pvals)

_marker_id_regex = re.compile(r'([^:]+):([0-9]+)_([-ATCG]+)/([-ATCG]+)(?:_(.+))?')
def parse_marker_id(marker_id):
    try:
        chr1, pos1, ref, alt, opt_info = _marker_id_regex.match(marker_id).groups()
        #assert chr1 == chr2
        #assert pos1 == pos2
    except:
        print(marker_id)
        raise
    return chr1, int(pos1), ref, alt

Variant = collections.namedtuple('Variant', 'chrom pos ref alt maf pval beta sebeta'.split())
def parse_variant_line(variant_line, column_indices):
    v = variant_line.split('\t')
    #assert v[1] == v[2]
    if v[column_indices["PVALUE"]] == 'NA' or v[column_indices["BETA"]] == 'NA':
        assert v[column_indices["PVALUE"]] == 'NA' and v[column_indices["BETA"]] == 'NA'
    else:
        chrom, pos, maf, pval, beta, sebeta = v[column_indices["#CHROM"]], int(v[column_indices["BEGIN"]]), float(v[column_indices["MAF"]]), float(v[column_indices["PVALUE"]]), float(v[column_indices["BETA"]]), float(v[column_indices["SEBETA"]])
        chrom2, pos2, ref, alt = parse_marker_id(v[column_indices["MARKER_ID"]])
        assert chrom == chrom2
        assert pos == pos2
        return Variant(chrom, pos, ref, alt, maf, pval, beta, sebeta)

def get_variants(f):
    header = f.readline().rstrip('\r\n').split('\t')
    if header[1] == "BEG":
        header[1] = "BEGIN"
    column_indices = {col_name: index for index, col_name in enumerate(header)}

    previously_seen_chroms, prev_chrom, prev_pos = set(), None, -1
    for variant_line in f:
        v = parse_variant_line(variant_line.rstrip('\r\n'), column_indices)
        if v is not None:
            if v.chrom == prev_chrom:
                assert v.pos >= prev_pos, (v.chrom, v.pos, prev_chrom, prev_pos)
            else:
                assert v.chrom not in previously_seen_chroms, (v.chrom, v.pos, prev_chrom, prev_pos)
                previously_seen_chroms.add(v.chrom)
            prev_chrom, prev_pos = v.chrom, v.pos
            yield v

def rounded_neglog10(pval):
    return round(-math.log10(pval) // NEGLOG10_PVAL_BIN_SIZE * NEGLOG10_PVAL_BIN_SIZE, NEGLOG10_PVAL_BIN_DIGITS)

def get_pvals_and_pval_extents(pvals):
    # expects that NEGLOG10_PVAL_BIN_SIZE is the distance between adjacent bins.
    pvals = sorted(pvals)
    extents = [[pvals[0], pvals[0]]]
    for p in pvals:
        if extents[-1][1] + NEGLOG10_PVAL_BIN_SIZE * 1.1 > p:
            extents[-1][1] = p
        else:
            extents.append([p,p])
    rv_pvals, rv_pval_extents = [], []
    for (start, end) in extents:
        if start == end:
            rv_pvals.append(start)
        else:
            rv_pval_extents.append([start,end])
    return (rv_pvals, rv_pval_extents)

def bin_variants(variants, binning_pval_threshold):
    bins = []
    unbinned_variants = []

    for variant in variants:
        if variant.pval < binning_pval_threshold:
            unbinned_variants.append({
                'chrom': variant.chrom,
                'pos': variant.pos,
                'ref': variant.ref,
                'alt': variant.alt,
                'maf': round_sig(variant.maf, 3),
                'pval': round_sig(variant.pval, 2),
                'beta': round_sig(variant.beta, 2),
                'sebeta': round_sig(variant.sebeta, 2),
            })

        else:
            if len(bins) == 0 or variant.chrom != bins[-1]['chrom']:
                # We need a new bin, starting with this variant.
                bins.append({
                    'chrom': variant.chrom,
                    'startpos': variant.pos,
                    'neglog10_pvals': set(),
                })
            elif variant.pos > bins[-1]['startpos'] + BIN_LENGTH:
                # We need a new bin following the last one.
                bins.append({
                    'chrom': variant.chrom,
                    'startpos': bins[-1]['startpos'] + BIN_LENGTH,
                    'neglog10_pvals': set(),
                })
            bins[-1]['neglog10_pvals'].add(rounded_neglog10(variant.pval))

    bins = [b for b in bins if len(b['neglog10_pvals']) != 0]
    for b in bins:
        b['neglog10_pvals'], b['neglog10_pval_extents'] = get_pvals_and_pval_extents(b['neglog10_pvals'])
        b['pos'] = int(b['startpos'] + BIN_LENGTH/2)
        del b['startpos']

    return bins, unbinned_variants


epacts_filename = sys.argv[1]
assert os.path.exists(epacts_filename)
out_filename = sys.argv[2]
assert os.path.exists(os.path.dirname(os.path.abspath(out_filename)))

with gzip.open(epacts_filename) as f:
    variants = get_variants(f)
    binning_pval_threshold = get_binning_pval_threshold(variants)

with gzip.open(epacts_filename) as f:
    variants = get_variants(f)
    variant_bins, unbinned_variants = bin_variants(variants, binning_pval_threshold)

rv = {
    'variant_bins': variant_bins,
    'unbinned_variants': unbinned_variants,
}

# Avoid getting killed while writing dest_filename, to stay idempotent despite me frequently killing the program
with open(out_filename, 'w') as f:
    json.dump(rv, f, sort_keys=True, indent=0)
print('{} -> {}'.format(epacts_filename, out_filename))
