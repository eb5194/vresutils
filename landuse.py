from __future__ import absolute_import

import numpy as np
import pandas as pd
import tempfile, os.path
import rasterio
import subprocess
from shutil import rmtree

from . import cachable, make_toModDir
from six import iteritems
from six.moves import map
toModDir = make_toModDir(__file__)

@cachable(ignore=set(('tmpdir',)))
def corine_label1(cutout, tmpdir=None):
    """
    Fractional landuse of LABEL1 categories of corine land cover 2006
    data for the REatlas grid of a `cutout`.

    Parameters
    ----------
    cutout : vreatlas.Cutout
        Used by corine_by_groups to determine the grid geometry
    tmpdir : None|str (default: None)
        If not None, where the intermediate files are kept

    Returns
    -------
    groups, landuse : [str], ndarray shape=(len(groups), LATS, LONS)
    """
    legend = pd.read_excel(toModDir('data/corine/clc_legend.xls')) \
               .set_index('GRID_CODE').loc[:47] # above 47, there is only NAN data

    def simplify_name(x):
        try:
            x = x[:x.index(' ')]
        except ValueError:
            pass
        return x.lower()

    groups = {simplify_name(k):v
              for k,v in iteritems(legend.groupby(legend.LABEL1).groups)}

    return list(groups), corine_by_groups(cutout, groups, tmpdir=tmpdir)

def corine_by_groups(cutout, groups, fn=toModDir('data/corine/g100_06.tif'), tmpdir=None):
    own_tmpdir = tmpdir is None
    if own_tmpdir:
        tmpdir = tempfile.mkdtemp()

    # Write out into different files, and convert those files using
    # gdalwarp. Second step should be converted to using rasterio at
    # some point, attempts for that are in
    # ~vres/data/jonas/playground/corine.ipynb.

    with rasterio.drivers():
        with rasterio.open(fn) as src:
            meta = src.meta.copy()
            meta.update(transform=src.meta['affine'],
                        compress='lzw')

            for group, indices in iteritems(groups):
                windows = src.block_windows(1)

                with rasterio.open(os.path.join(tmpdir, '{}.tif'.format(group)), 'w', **meta) as dst:
                    for idx, window in windows:
                        src_data, = src.read(window=window)
                        dst_data = np.in1d(src_data.ravel(), indices).astype("uint8").reshape(src_data.shape)
                        dst.write_band(1, dst_data, window=window)



        minc, maxc = cutout.grid_coordinates()[[0,-1]]
        span = (maxc - minc)/(np.asarray(cutout.shape)[[1,0]]-1)
        minx, miny = minc - span/2.
        maxx, maxy = maxc + span/2.

        for group in groups:
            ret = subprocess.call(['gdalwarp', '-overwrite',
                                   '-t_srs', 'EPSG:4326',
                                   '-te', str(minx), str(miny), str(maxx), str(maxy),
                                   '-ts', str(cutout.shape[1]), str(cutout.shape[0]),
                                   '-r', "average",
                                   '-wt', 'Float64',
                                   '-ot', 'Float64',
                                   '-srcnodata', 'None',
                                   '-dstnodata', 'None',
                                   os.path.join(tmpdir, '{}.tif'.format(group)),
                                   os.path.join(tmpdir, '{}_avg.tif'.format(group))])
            assert ret == 0, "gdalwarp for group '{}' did not return successfully.".format(group)


        def load_avg(group):
            with rasterio.open(os.path.join(tmpdir, '{}_avg.tif'.format(group))) as avg:
                return avg.read()[0]
        landuse = np.asarray(list(map(load_avg, groups)))

    if own_tmpdir:
        rmtree(tmpdir, ignore_errors=True)

    return landuse

# Mappings between CORINE Label1 and fractional use
wind = pd.Series(dict(agricultural=1.0, forest=1.0, wetlands=1.0))
solar = pd.Series(dict(artificial=1.0, agricultural=1.0, wetlands=1.0))

def potential(mapping, cutout, func=corine_label1):
    """
    Returns the landuse potential, the fraction of the usable area

    Parameters
    ----------
    mapping : pd.Series
        Map between group and fraction
    cutout : vreatlas.Cutout
        Used by func to determine the cell geometry
    func : function : vreatlas.Cutout -> (groups, landuse)
        Default is corine_label1

    Example
    -------
    cutout = vreatlas.Cutout(cutoutname='Europe_2011_2014', username='becker')
    potential = vlanduse.potential(vlanduse.wind, cutout)
    """
    groups, landuse = func(cutout)
    return np.dot(landuse.transpose((1,2,0)), mapping.reindex(groups).fillna(0.))