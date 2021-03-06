#!/usr/bin/env python

from scipy import spatial
import os
import math
from astropy.coordinates import SkyCoord, Angle
from astropy import units as u
from astropy.io import fits
from astropy.table import Table, Column
import argparse
import ConfigParser
import numpy as np
from collections import OrderedDict
from scipy.interpolate import interp1d
from collections import defaultdict
import itertools
import matplotlib.pyplot as plt

import sys
sys.path.append(os.path.join(os.environ['PYP_BEAGLE'], "PyP-BEAGLE"))
from beagle_utils import BeagleDirectories, extract_IDs
import beagle_multiprocess

from pathos.multiprocessing import ProcessingPool 

deltaEvidence_lim = 6.
deltaZ_lim = 0.5
__logBase10of2 = 3.010299956639811952137388947244930267681898814621085413104274611e-1

results_dir = ""

def RoundToSigFigs( x, sigfigs ):
    """
    Rounds the value(s) in x to the number of significant figures in sigfigs.

    Restrictions:
    sigfigs must be an integer type and store a positive value.
    x must be a real value or an array like object containing only real values.
    """
    if not ( type(sigfigs) is int or np.issubdtype(sigfigs, np.integer)):
        raise TypeError( "RoundToSigFigs: sigfigs must be an integer." )

    if not np.all(np.isreal( x )):
        raise TypeError( "RoundToSigFigs: all x must be real." )

    if sigfigs <= 0:
        raise ValueError( "RoundtoSigFigs: sigfigs must be positive." )

    mantissas, binaryExponents = np.frexp( x )

    decimalExponents = __logBase10of2 * binaryExponents
    intParts = np.floor(decimalExponents)

    mantissas *= 10.0**(decimalExponents - intParts)

    return np.around( mantissas, decimals=sigfigs - 1 ) * 10.0**intParts

def find_nearest(array,value):
    idx = np.searchsorted(array, value, side="left")
    if idx > 0 and (idx == len(array) or math.fabs(value - array[idx-1]) < math.fabs(value - array[idx])):
        dist = np.abs(value-array[idx-1])
        return idx-1, dist
    else:
        dist = np.abs(value-array[idx])
        return idx, dist

def get_mode_rows(ID, param_names, param_indices, mode_index):

    file_name = os.path.join(ID + "_BEAGLE_MNpost_separate.dat")

    n_empty = 0
    prev_is_empty = False
    values = defaultdict(list)
    with open(file_name , 'r') as f:

        for line in f:

            is_empty = False
            if not line.strip():
                is_empty = True

            if is_empty and not prev_is_empty:
                n_empty += 1

            prev_is_empty = is_empty

            if not is_empty and n_empty == (int(mode_index)):
                split = line.split()
                for name, indx in zip(param_names, param_indices):
                    value = split[1+indx]
                    values[name].append(float(value))

    n = len(values[param_names[0]])
    n_par = len(param_names)
    np_values = np.zeros([n, n_par])
    for i, (key, value) in enumerate(values.iteritems()):
        np_values[:,i] = value

    tree = spatial.cKDTree(np_values)


    file_name = os.path.join(results_dir, ID + "_BEAGLE.fits.gz")

    hdulist = fits.open(file_name)
    n = len(hdulist['POSTERIOR PDF'].data['probability'])
    full_values = np.zeros([n, n_par])
    for i, name in enumerate(param_names):
        full_values[:,i] = hdulist['POSTERIOR PDF'].data[name]

    #sor = np.argsort(full_values)

    full_indx = np.arange(len(full_values))
    #full_values_sor = full_values[sor]
    #full_indx = full_indx[sor]

    rows = list()

    distances, indiced = tree.query(full_values)

    max_distance = 1.E-06
    
    #print "distance: ", distances
    #plt.hist(np.log10(distances), 50, normed=1, facecolor='green', alpha=0.75)
    #plt.show()
    #pause    

    loc = np.where(distances <= max_distance)[0]
    rows = full_indx[loc]

    hdulist.close()

    return rows
    
def extract_data(ID, n_par, redshift_index, redshift_type=None):

    file_name = os.path.join(results_dir, str(ID) + "_BEAGLE_MNstats.dat")

    if not os.path.isfile(file_name):
        return None

    # This number include the posterior mean, maximum likelihood and
    # maximum a posteriori for each parameter + the headers
    n_lines_per_mode = 8 + n_par*3

    # Useful information for the first mode start at line 11 (in Python
    # we count from 0)
    first_line = 10

    # Now we read the evidence, post mean, maximum likelihood and map for each mode
    f = open(file_name , 'r')
    outData = OrderedDict()
    post_sig = list()
    post_mean = list()
    max_likelihood = list()
    max_a_post = list()
    logEvidence = list()
    mode_count = 1

    for i, line in enumerate(f):
        
        # Evidence
        if i == first_line:
            logEv = float(line.split()[2])
        # Posterior mean for each parameter 
        elif((i >= first_line+3) and (i < first_line+3+n_par)):
            post_mean.append(float(line.split()[1])) 
            post_sig.append(float(line.split()[2])) 
        # Maximum likelihood for each parameter 
        elif((i >= first_line+6+n_par) and (i < first_line+6+2*n_par)):
            max_likelihood.append(float(line.split()[1])) 
        # Maximum a posteriori for each parameter 
        elif((i >= first_line+9+2*n_par) and (i < first_line+9+3*n_par)):
            max_a_post.append(float(line.split()[1])) 

        # Once you've read the data for the first mode, put them into
        # the MultiNestObject!
        if i == (first_line + n_lines_per_mode):
            key = "mode_"+str(mode_count)
            outData[key] = {"evidence":logEv, 
                    "posterior_mean":post_mean, 
                    "posterior_sigma":post_sig, 
                    "max_likelihood":max_likelihood,
                    "max_a_post":max_a_post}

            post_mean = list()
            post_sig = list()
            max_likelihood = list()
            max_a_post = list()
            logEvidence.append(logEv)
            first_line += n_lines_per_mode + 5
            mode_count = mode_count + 1

    f.close()
    sor = np.arange(len(logEvidence))
    data = OrderedDict()
    if len(logEvidence) > 1:
        sor = np.argsort(logEvidence)
        key_1 = outData.keys()[sor[-1]]
        key_2 = outData.keys()[sor[-2]]

        deltaEvidence = outData[key_1]["evidence"] - outData[key_2]["evidence"]
        deltaZ = outData[key_1]["posterior_mean"][redshift_index-1] - outData[key_2]["posterior_mean"][redshift_index-1]
        #print 'deltaZ: ', deltaZ, outData[key_1]["posterior_mean"][1], outData[key_2]["posterior_mean"][1]

        if deltaEvidence < deltaEvidence_lim and abs(deltaZ) > deltaZ_lim:
            if redshift_type is not None:
                z_1 = outData[key_1]["posterior_mean"][redshift_index-1]
                z_2 = outData[key_2]["posterior_mean"][redshift_index-1]
                if z_1 > z_2:
                    key_max = key_1
                    key_min = key_2
                else:
                    key_max = key_2
                    key_min = key_1
    
                if redshift_type == "high":
                    data[key_max] = outData[key_max]
                    data[key_min] = outData[key_min]
                    return data
                elif  redshift_type == "low":
                    data[key_min] = outData[key_min]
                    data[key_max] = outData[key_max]
                    return data

            data[key_1] = outData[key_1]
            data[key_2] = outData[key_2]
            return data

        data[key_1] = outData[key_1]
        return data

    return outData

def get1DInterval(ID, param_names, levels=[68., 95.]):

    suffix = BeagleDirectories.suffix + '.fits.gz'

    full_path = os.path.join(args.results_dir, str(ID)+'_'+suffix)
    if not os.path.isfile(full_path):
        return None

    param_values = OrderedDict()
    with fits.open(full_path) as f:
        probability = f['POSTERIOR PDF'].data['probability']
        for name in param_names:
            param_values[name] = f['POSTERIOR PDF'].data[name]

    output = OrderedDict()
    for key, value in param_values.iteritems():

        sort_ = np.argsort(value)

        cumul_pdf = np.cumsum(probability[sort_])
        cumul_pdf /= cumul_pdf[len(cumul_pdf)-1]

        # Get the interpolant of the cumulative probability
        f_interp = interp1d(cumul_pdf, value[sort_])

        # You shoud integrate rather than summing here
        mean = np.sum(probability * value) / np.sum(probability)

        median = f_interp(0.5)

        interval = OrderedDict()
        for lev in levels:

            low, high = f_interp([0.5*(1.-lev/100.), 1.-0.5*(1.-lev/100.)])
            interval[str(lev)] = np.array([low,high])

        output[key] = {'mean':mean, 'median':median, 'regions':interval}

    return output

if __name__ == '__main__':

    parser = argparse.ArgumentParser()

    parser.add_argument(
        '-i', '--input',
        help="Name of the input catalogue", 
        action="store", 
        type=str, 
        dest="inputCat", 
        required=True
    )

    parser.add_argument(
        '--input-coord',
        help="",
        action="store", 
        type=str, 
        nargs=2,
        dest="input_coord",
        default=("deg", "deg")
    )

    parser.add_argument(
        '--beagle-coord',
        help="",
        action="store", 
        type=str, 
        nargs=2,
        dest="beagle_coord",
        default=("deg", "deg")
    )

    parser.add_argument(
        '-r', '--results-dir',
        help="Directory containing BEAGLE results",
        action="store", 
        type=str, 
        dest="results_dir", 
        required=True
    )

    parser.add_argument(
        '-p', '--parameter-file',
        help="Parametr file used in the BEAGLE run",
        action="store", 
        type=str, 
        dest="param_file",
        required=True
    )

    parser.add_argument(
        '-nproc',
        help="Number of processors to use",
        action="store", 
        type=int, 
        dest="nproc",
        default=-1
    )

    parser.add_argument(
        '--credible-regions',
        help="Credible regions to calculate",
        action="store", 
        type=float, 
        nargs='+',
        dest="credible_regions"
    )


    # Get parsed arguments
    args = parser.parse_args()

    results_dir = args.results_dir

    # Read the input catalogue
    inputData = Table.read(args.inputCat)

    # ID in the input catalogue
    input_IDs = extract_IDs(inputData)

    # Read parameter file
    config = ConfigParser.SafeConfigParser()

    # Search for the parameter file in the results directory
    if os.path.isabs(args.param_file):
        param_file = args.param_file
    else:
        param_file = os.path.join(args.results_dir, 'BEAGLE-input-files', args.param_file)

    config.read(param_file)

    file_name = os.path.expandvars(config.get('main', 'PHOTOMETRIC CATALOGUE'))
    BeaglePhotCat = fits.open(file_name)[1].data

    # ID in the photometric catalogue fitted by Beagle
    Beagle_IDs = extract_IDs(BeaglePhotCat)

    # Extract RA DEC of input catalogue
    if args.input_coord[0] == "deg":
        ra = Angle(np.array(inputData['RA']), unit=u.deg)
    elif args.input_coord[0] == "hourangle":
        ra = Angle(np.array(inputData['RA']), unit=u.hourangle)

    if args.input_coord[1] == "deg":
        dec = Angle(np.array(inputData['DEC']), unit=u.deg)
    elif args.input_coord[1] == "hourangle":
        dec = Angle(np.array(inputData['DEC']), unit=u.hourangle)

    inputCoord = SkyCoord(ra=ra, dec=dec)  

    # Extract RA DEC of catalogue used in Beagle run
    if args.beagle_coord[0] == "deg":
        ra = Angle(np.array(BeaglePhotCat['RA']), unit=u.deg)
    elif args.beagle_coord[0] == "hourangle":
        ra = Angle(np.array(BeaglePhotCat['RA']), unit=u.hourangle)

    if args.beagle_coord[1] == "deg":
        dec = Angle(np.array(BeaglePhotCat['DEC']), unit=u.deg)
    elif args.beagle_coord[1] == "hourangle":
        dec = Angle(np.array(BeaglePhotCat['DEC']), unit=u.hourangle)

    BeagleCoord = SkyCoord(ra=ra, dec=dec)

    n_input = len(inputData.field(0))
    # Columns to be added to the catalogues
    param_names = ["redshift", "mass"]

    dictKeys = OrderedDict()

    dictKeys["ID_input"] = {"type":"S15", "format":"s"}
    dictKeys["ID_Beagle"] = {"type":"S15", "format":"s"}
    dictKeys["distance"] = {"type":np.float32, "format":".3f"}

    for name in param_names:
        for j in range(2):
            suff = str(j+1)
            key = name+"_beagle_"+suff
            dictKeys[key] = {"type":np.float32, "format":".3f"}

            key = name+"_beagle_err_"+suff
            dictKeys[key] = {"type":np.float32, "format":".3f"}

        if args.credible_regions is not None:
            for region in args.credible_regions:
                key = name + "_" + str(region) + "_low"
                dictKeys[key] = {"type":np.float32, "format":".3f"}
                key = name + "_" + str(region) + "_up"
                dictKeys[key] = {"type":np.float32, "format":".3f"}
    
    paramDict = OrderedDict()
    # Determine number of free parameters by counting columns in Beagle output file
    suffix = BeagleDirectories.suffix + '.fits.gz'
    for file in sorted(os.listdir(args.results_dir)):
        full_path = os.path.join(args.results_dir, file)
        if file.endswith(suffix) and os.path.getsize(full_path) > 0:
            with fits.open(full_path) as f:
                n_par = len(f["POSTERIOR PDF"].data.dtype.names)-2
                
                for name in param_names:
                    for i, col_name in enumerate(f["POSTERIOR PDF"].data.dtype.names):
                        if name == col_name:
                            paramDict[name] = (i+1)-2
                            break


            break

    dictKeys["deltaEvidence"] = {"type":np.float32, "format":".2f"}
    dictKeys["KF_flag"] = {"type":np.int, "format":"1d"}
    dictKeys["P1/P2"] = {"type":np.float32, "format":".3e"}

    newCols = OrderedDict()

    for key, value in dictKeys.iteritems():
        Type = value["type"]
        if isinstance(Type, str):
            newCols[key] = np.full(n_input, "-99", Type)
        else:
            newCols[key] = np.full(n_input, -99, Type)

    # Match RA DEC of input catalogue to RA DEC of catalogue used in Beagle run
    idx, d2d, d3d = inputCoord.match_to_catalog_sky(BeagleCoord)  
    mask = np.zeros(n_input, dtype=bool)
    ok = np.where(d2d.arcsecond <= 0.2)[0]
    input_idx = range(n_input)
    input_idx = np.array(input_idx)[ok]

    match_ok = idx[ok]
    n_ok = len(match_ok)
    # Put oriignal (input catalgoue) IDs in the output catalogue
    newCols["ID_input"] = np.array(input_IDs)

    # Put IDs and distances of matched objects
    newCols["ID_Beagle"][ok] = np.array(Beagle_IDs[match_ok])
    newCols["distance"][ok] = d2d.arcsecond[ok]

    #print "-------> ", get1DInterval(Beagle_IDs[match_ok[0]], param_names=param_names, levels=[68., 95., 99.7])

    #pause
    # If the user does not specify the number of processors to be used, assume that it is a serial job
    if args.credible_regions is not None:
        data_cred_region = list()

    data = list()
    if args.nproc <= 0:

        for indx in match_ok:
            ID = Beagle_IDs[indx]
            d = extract_data(ID, 
                    n_par=n_par, 
                    redshift_index=paramDict["redshift"]
                    )

            data.append(d)

            if args.credible_regions is not None:
                c = get1DInterval(ID, 
                        param_names=param_names, 
                        levels=args.credible_regions
                        )

                data_cred_region.append(c)
    
    # Otherwise you use pathos to run in parallel on multiple CPUs
    else:

        # Set number of parellel processes to use
        pool = ProcessingPool(nodes=args.nproc)

        # Launch the actual calculation on multiple processesors
        data = pool.map(extract_data, 
            Beagle_IDs[match_ok],
            (n_par,)*n_ok,
            (paramDict["redshift"],)*n_ok
            )

        if args.credible_regions is not None:
            data_cred_region = pool.map(get1DInterval,
                    Beagle_IDs[match_ok],
                    (param_names,)*n_ok,
                    (args.credible_regions,)*n_ok
                    )

    for i, indx in enumerate(input_idx):

        d = data[i]

        if d is None:
            continue

        for j, (key, value) in enumerate(d.iteritems()):

            for name, row_index in paramDict.iteritems():
                suff = str(j+1)
                newCols[name+"_beagle_"+suff][indx] = value["posterior_mean"][row_index-1]
                newCols[name+"_beagle_err_"+suff][indx] = value["posterior_sigma"][row_index-1]

            if j == 0:
                deltaEvidence = value["evidence"]
            else:
                deltaEvidence -= value["evidence"]

        if j > 0:
            newCols["deltaEvidence"][indx] = deltaEvidence
            newCols["P1/P2"][indx] = np.exp(-deltaEvidence)

            if 2.*deltaEvidence < 2.:
                newCols["KF_flag"][indx] = 0
            elif 2.*deltaEvidence < 6.:
                newCols["KF_flag"][indx] = 1
            elif 2.*deltaEvidence < 10.:
                newCols["KF_flag"][indx] = 2
            else:
                newCols["KF_flag"][indx] = 3
    
        if args.credible_regions is not None:
            c = data_cred_region[i]
            if c is not None:
                for name in param_names:
                    for region in args.credible_regions:
                        key = name + "_" + str(region) + "_low"
                        newCols[key][indx] = c[name]["regions"][str(region)][0]
                        key = name + "_" + str(region) + "_up"
                        newCols[key][indx] = c[name]["regions"][str(region)][1]


    myCols = list()
    for i, (key, col) in enumerate(newCols.iteritems()):
        tmpCol = Column(col, name=key, dtype=dictKeys[key]["type"], format='%'+dictKeys[key]['format'])
        myCols.append(tmpCol)

    newTable = Table(myCols)

    file_name = os.path.splitext(args.inputCat)[0] + '_Beagle_VAC.txt'
    print "Output (ASCII) file_name: ", file_name
    newTable.write(file_name, format="ascii.commented_header")

    file_name = os.path.splitext(args.inputCat)[0] + '_Beagle_VAC.fits'
    print "Output (FITS) file_name: ", file_name
    newTable.write(file_name, format="fits", overwrite=True)
