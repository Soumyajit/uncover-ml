import os
import tables
import numpy as np
import shapefile
import rasterio
import subprocess
from glob import glob

from uncoverml import geoio, patch, streams
from uncoverml.scripts.maketargets import main as maketargets
from uncoverml.scripts.cvindexer import main as cvindexer
from uncoverml.scripts.extractfeats import main as extractfeats


def test_make_targets(make_shp_gtiff):

    fshp, _ = make_shp_gtiff
    field = "lon"

    maketargets.callback(shapefile=fshp, fieldname=field, outfile=None,
                         quiet=False)

    fhdf5 = os.path.splitext(fshp)[0] + "_" + field + ".hdf5"

    assert os.path.exists(fhdf5)

    with tables.open_file(fhdf5, mode='r') as f:
        lon = np.array([i for i in f.root.lon])
        Longitude = np.array([l for l in f.root.Longitude])

    assert np.allclose(lon, Longitude)


def test_cvindexer_shp(make_shp_gtiff):

    fshp, _ = make_shp_gtiff
    folds = 6
    field = "lon"
    fshp_hdf5 = os.path.splitext(fshp)[0] + ".hdf5"
    fshp_targets = os.path.splitext(fshp)[0] + "_" + field + ".hdf5"

    # Make crossval with shapefile
    cvindexer.callback(targetfile=fshp, outfile=fshp_hdf5, folds=6,
                       quiet=True)
    # Make target file
    maketargets.callback(shapefile=fshp, fieldname=field, outfile=fshp_targets,
                         quiet=False)

    # Read in resultant HDF5
    with tables.open_file(fshp_hdf5, mode='r') as f:
        hdfcoords = np.array([(x, y) for x, y in zip(f.root.Longitude,
                                                     f.root.Latitude)])
        finds = np.array([i for i in f.root.FoldIndices])

    # Validate order is consistent with shapefile
    f = shapefile.Reader(fshp)
    shpcoords = np.array([p.points[0] for p in f.shapes()])

    assert np.allclose(shpcoords, hdfcoords)

    # Test we have the right number of folds
    assert finds.min() == 0
    assert finds.max() == (folds - 1)


def test_cvindexer_hdf(make_shp_gtiff):

    fshp, _ = make_shp_gtiff
    folds = 6
    field = "lon"
    fshp_hdf5 = os.path.splitext(fshp)[0] + ".hdf5"
    fshp_targets = os.path.splitext(fshp)[0] + "_" + field + ".hdf5"

    # Make crossval with target file
    cvindexer.callback(targetfile=fshp_targets, outfile=fshp_hdf5, folds=6,
                       quiet=True)

    # Read in resultant HDF5
    with tables.open_file(fshp_hdf5, mode='r') as f:
        hdfcoords = np.array([(x, y) for x, y in zip(f.root.Longitude,
                                                     f.root.Latitude)])
        finds = np.array([i for i in f.root.FoldIndices])

    # Validate order is consistent with target file
    with tables.open_file(fshp_targets, mode='r') as f:
        targcoords = np.array([(x, y) for x, y in zip(f.root.Longitude,
                                                      f.root.Latitude)])

    assert np.allclose(targcoords, hdfcoords)

    # Test we have the right number of folds
    assert finds.min() == 0
    assert finds.max() == (folds - 1)


def test_extractfeats(make_shp_gtiff):

    fshp, ftif = make_shp_gtiff
    chunks = 4
    outdir = os.path.dirname(fshp)
    name = "fchunk"

    # Extract features from gtiff
    extractfeats.callback(geotiff=ftif, name=name, targets=None, redisdb=0,
                          redishost='localhost', redisport=6379,
                          standalone=True, chunks=chunks, patchsize=0,
                          quiet=False, outputdir=outdir)

    # Now compare extracted features to geotiff
    with rasterio.open(ftif, 'r') as f:
        I = np.transpose(f.read(), [2, 1, 0])

    ffiles = glob(os.path.join(outdir, name + "*"))
    ffiles.reverse()
    efeats = []
    for fname in ffiles:
        print(fname)
        with tables.open_file(fname, 'r') as f:
            strip = [fts for fts in f.root.features]
            efeats.append(np.reshape(strip, (I.shape[0], -1, I.shape[2])))

    efeats = np.concatenate(efeats, axis=1)

    assert I.shape == efeats.shape
    assert np.allclose(I, efeats)


def test_extractfeats_worker(make_shp_gtiff):

    fshp, ftif = make_shp_gtiff
    chunks = 4
    outdir = os.path.dirname(fshp)
    name = "fchunk_worker"

    predis = None
    pworker = None

    # Start redis
    try:
        redisargs = ["redis-server", "--port", "6379"]
        predis = subprocess.Popen(redisargs, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT)

        _proc_ready(predis)

        # Start the worker
        pworker = subprocess.Popen("uncoverml-worker", stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT)
        _proc_ready(pworker)

        # Extract features from gtiff
        extractfeats.callback(geotiff=ftif, name=name, targets=None, redisdb=0,
                              redishost='localhost', redisport=6379,
                              standalone=True, chunks=chunks, patchsize=0,
                              quiet=False, outputdir=outdir)

    finally:
        # Kill worker
        if predis is not None:
            predis.terminate()
        if pworker is not None:
            pworker.terminate()

    # ffiles = glob(os.path.join(outdir, name + "*"))
    for i in range(chunks):
        fname = os.path.join(outdir, "{}_{}.hdf5".format(name, i))
        assert os.path.exists(fname)


def _proc_ready(proc, waitime=10):

    nbsr = streams.NonBlockingStreamReader(proc.stdout)

    for i in range(waitime):
        try:
            for line in iter(nbsr.readline, None):
                if "ready" in line.decode():
                    return
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            pass
        except Exception:
            proc.terminate()
            raise

    raise RuntimeError("Process {} never ready!".format(proc.args))