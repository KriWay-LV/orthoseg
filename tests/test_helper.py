"""
Helper functions for all tests.
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import ClassVar

import gdown
import geopandas as gpd
from shapely import geometry as sh_geom

sampleprojects_dir = Path(__file__).resolve().parent.parent / "sample_projects"


class SampleProjectFootball:
    project_dir = sampleprojects_dir / "footballfields"
    predict_config_path = project_dir / "footballfields_BEFL-2019_test.ini"
    train_config_path = project_dir / "footballfields_train_test.ini"

    @staticmethod
    def download_model(dst_dir):
        cache_dir = Path(tempfile.gettempdir()) / "orthoseg_test_cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        model_name = "footballfields_01_0.97392_201.hdf5"
        model_hyperparams_name = "footballfields_01_hyperparams.json"
        model_modeljson_name = "footballfields_01_model.json"

        model_hdf5_cache_path = cache_dir / model_name
        if not model_hdf5_cache_path.exists():
            gdown.download(
                id="1UlNorZ74ADCr3pL4MCJ_tnKRNoeZX79g",
                output=str(model_hdf5_cache_path),
            )
        if not (dst_dir / model_name).exists():
            os.link(model_hdf5_cache_path, dst_dir / model_name)

        model_hyperparams_cache_path = cache_dir / model_hyperparams_name
        if not model_hyperparams_cache_path.exists():
            gdown.download(
                id="1NwrVVjx9IsjvaioQ4-bkPMrq7S6HeWIo",
                output=str(model_hyperparams_cache_path),
            )
        if not (dst_dir / model_hyperparams_name).exists():
            os.link(model_hyperparams_cache_path, dst_dir / model_hyperparams_name)

        model_modeljson_cache_path = cache_dir / model_modeljson_name
        if not model_modeljson_cache_path.exists():
            gdown.download(
                id="1LNPLypM5in3aZngBKK_U4Si47Oe97ZWN",
                output=str(model_modeljson_cache_path),
            )
        if not (dst_dir / model_modeljson_name).exists():
            os.link(model_modeljson_cache_path, dst_dir / model_modeljson_name)


class SampleProjectTemplate:
    project_dir = sampleprojects_dir / "project_template"


class TestData:
    dir = Path(__file__).resolve().parent / "data"

    classes: ClassVar = {
        "background": {
            "labelnames": ["ignore_for_train", "background"],
            "weight": 1,
            "burn_value": 0,
        },
        "test_classname1": {"labelnames": ["testlabel1"], "weight": 1, "burn_value": 1},
        "test_classname2": {"labelnames": ["testlabel2"], "weight": 1, "burn_value": 1},
    }
    image_pixel_x_size = 0.25
    image_pixel_y_size = 0.25
    image_pixel_width = 512
    image_pixel_height = 512
    image_crs_width = image_pixel_width * image_pixel_x_size
    image_crs_height = image_pixel_height * image_pixel_y_size
    crs_xmin = 150000
    crs_ymin = 150000
    crs = "EPSG:31370"
    location = sh_geom.box(
        crs_xmin,
        crs_ymin,
        crs_xmin + (image_pixel_width * image_pixel_x_size),
        crs_ymin + (image_pixel_height * image_pixel_y_size),
    )
    location_invalid = sh_geom.Polygon(
        [
            (crs_xmin, crs_ymin),
            (crs_xmin + image_crs_width, crs_ymin),
            (crs_xmin + image_crs_width, crs_ymin + image_crs_height),
            (crs_xmin, crs_ymin + image_crs_height),
            (
                crs_xmin + image_pixel_x_size,
                crs_ymin + image_crs_height + image_pixel_y_size,
            ),
            (crs_xmin, crs_ymin),
        ]
    )
    polygon = location
    polygon_invalid = location_invalid
    locations_gdf = gpd.GeoDataFrame(
        {
            "geometry": [location, location, location, location],
            "traindata_type": ["train", "validation", "test", "todo"],
            "path": "/tmp/locations.gdf",
        },
        crs="epsg:31370",
    )
    polygons_gdf = gpd.GeoDataFrame(
        {
            "geometry": [polygon, polygon],
            "classname": ["testlabel1", "testlabel2"],
            "path": "/tmp/polygons.gdf",
        },
        crs="epsg:31370",
    )


def create_tempdir(base_dirname: str, parent_dir: Path | None = None) -> Path:
    # Parent
    if parent_dir is None:
        parent_dir = Path(tempfile.gettempdir())

    for i in range(1, 999999):
        try:
            tempdir = parent_dir / f"{base_dirname}_{i:06d}"
            tempdir.mkdir(parents=True)
            return Path(tempdir)
        except FileExistsError:
            continue

    raise Exception(
        "Wasn't able to create a temporary dir with basedir: "
        f"{parent_dir / base_dirname}"
    )


def init_test_for_debug(test_module_name: str) -> Path:
    # Init logging
    logging.basicConfig(
        format="%(asctime)s.%(msecs)03d|%(levelname)s|%(name)s|%(message)s",
        datefmt="%H:%M:%S",
        level=logging.INFO,
    )

    # Prepare tmpdir
    tmp_basedir = Path(tempfile.gettempdir()) / test_module_name
    tmpdir = create_tempdir(parent_dir=tmp_basedir, base_dirname="debugrun")

    """
    if tmpdir.exists():
        shutil.rmtree(tmpdir)
    tmpdir.mkdir(parents=True, exist_ok=True)
    """

    return tmpdir
