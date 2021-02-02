# -*- coding: utf-8 -*-
"""
Module with functions for post-processing prediction masks towards polygons.
"""

import logging
from pathlib import Path
import shapely.geometry as sh_geom
import shutil
from typing import Optional

# Evade having many info warnings about self intersections from shapely
logging.getLogger('shapely.geos').setLevel(logging.WARNING)
from geofileops import geofileops
from geofileops import geofile
import geopandas as gpd
import numpy as np
import pandas as pd
import rasterio as rio
import rasterio.features as rio_features
import rasterio.transform as rio_transform
import shapely as sh
import shapely.geometry as sh_geom
import tensorflow as tf

from orthoseg.util import vector_util
import geofileops.util.vector_util as gfo_vector_util

#-------------------------------------------------------------
# First define/init some general variables/constants
#-------------------------------------------------------------
# Get a logger...
logger = logging.getLogger(__name__)
#logger.setLevel(logging.DEBUG)

#-------------------------------------------------------------
# The real work
#-------------------------------------------------------------

#-------------------------------------------------------------
# Postprocess to use on all vector outputs
#-------------------------------------------------------------

def postprocess_predictions(
        input_path: Path,
        output_path: Path,
        dissolve: bool,
        dissolve_tiles_path: Optional[Path] = None,
        force: bool = False):
    """
    Postprocesses the input prediction as specified. 
        
    Args
        input_dir: the dir where all geojson files can be found. All geojson 
                files will be searched for recursively.
        output_filepath: the filepath where the output file(s) will be written.
        dissolve (bool): True if a dissolve needs to be applied
        dissolve_tiles_path (PathLike, optional): Path to a geofile containing 
            the tiles to be used for the dissolve. Defaults to None.
        force: False to skip results that already exist, true to
               ignore existing results and overwrite them           
    """
    # Init
    if not input_path.exists():
        raise Exception(f"input_path does not exist: {input_path}")

    # Dissolve the predictions if needed
    if dissolve:
        clip_on_tiles = False
        if dissolve_tiles_path is not None:
            clip_on_tiles = True
        curr_output_path = output_path.parent / f"{output_path.stem}_dissolve{output_path.suffix}"
        
        # If column classname present, group on it...
        layerinfo = geofile.get_layerinfo(input_path)
        if 'classname' in layerinfo.columns:
            groupby_columns = ['classname']
            columns = groupby_columns
        else:
            groupby_columns = None
            columns = []

        # Now we can dissolve
        geofileops.dissolve(
                input_path=input_path,
                tiles_path=dissolve_tiles_path,
                output_path=curr_output_path,
                groupby_columns=groupby_columns,
                columns=columns,
                explodecollections=True,
                clip_on_tiles=clip_on_tiles,
                force=force)

        # Add columns with area and nbcoords
        # Terrible performance using geofileops with libspatialite < 5.0.0,
        # so temporary calculate it using geopandas.
        """
        geofile.add_column(path=curr_output_path, 
                name='area', type='real', expression='ST_Area(geom)')
        geofile.add_column(path=curr_output_path, 
                name='nbcoords', type='integer', expression='ST_NPoints(geom)')
        """

        diss_gdf = geofile.read_file(curr_output_path)
        diss_gdf['area'] = diss_gdf.geometry.area

        def numberpoints(geometry: sh_geom.base.BaseGeometry) -> int:
            
            nb_points = 0
            if(isinstance(geometry, sh_geom.multipolygon.MultiPolygon)):
                for polygon in geometry:
                    nb_points += len(polygon.exterior.coords)
                    for ring in polygon.interiors:
                        nb_points += len(ring.coords)
            elif(isinstance(geometry, sh_geom.polygon.Polygon)):
                nb_points += len(geometry.exterior.coords)
                for ring in geometry.interiors:
                    nb_points += len(ring.coords)
            else:
                raise Exception(f"geometry type is not supported: {geometry.geom_type}")
            
            return nb_points

        diss_gdf['nbcoords'] = diss_gdf.geometry.apply(lambda geom: numberpoints(geom))
        geofile.remove(curr_output_path)
        geofile.to_file(diss_gdf, curr_output_path)

def read_prediction_file(
        filepath: Path,
        border_pixels_to_ignore: int = 0) -> Optional[gpd.GeoDataFrame]:
    ext_lower = filepath.suffix.lower()
    if ext_lower == '.geojson':
        return geofile.read_file(filepath)
    elif ext_lower == '.tif':
        return polygonize_pred_from_file(filepath, border_pixels_to_ignore)
    else:
        raise Exception(f"Unsupported extension: {ext_lower}")

def to_binary_uint8(
        in_arr: np.ndarray, 
        thresshold_ok: int = 128) -> np.ndarray:

    # Check input parameters
    if in_arr.dtype != np.uint8:
        raise Exception("Input should be dtype = uint8, not: {in_arr.dtype}")
        
    # First copy to new numpy array, otherwise input array is changed
    out_arr = np.copy(in_arr)
    out_arr[out_arr >= thresshold_ok] = 255
    out_arr[out_arr < thresshold_ok] = 0
    
    return out_arr

def postprocess_for_evaluation(
        image_filepath: Path,
        image_crs: str,
        image_transform,
        image_pred_filepath: Path,
        image_pred_uint8_cleaned_bin: np.ndarray,
        class_id: int,
        class_name: str,
        nb_classes: int,
        output_dir: Path,
        output_suffix: str = None,
        input_image_dir: Optional[Path] = None,
        input_mask_dir: Optional[Path] = None,
        border_pixels_to_ignore: int = 0,
        force: bool = False):
    """
    This function postprocesses a prediction to make it easy to evaluate 
    visually if the result is OK by creating images of the different stages of 
    the prediction logic by creating the following output:
        - the input image
        - the mask image as digitized in the train files (if available)
        - the "raw" prediction image
        - the "raw" polygonized prediction, as an image
        - the simplified polygonized prediction, as an image

    The filenames start with a prefix:
        - if a mask is available, the % overlap between the result and the mask
        - if no mask is available, the % of pixels that is white
    
    Args
        
    """
    
    logger.debug(f"Start postprocess for {image_pred_filepath}")
    all_black = False
    try:      
        
        # If the image wasn't saved, it must have been all black
        if image_pred_filepath is None:
            all_black = True

        # Make sure the output dir exists...
        output_dir.mkdir(parents=True, exist_ok=True)
                
        # Determine the prefix to use for the output filenames
        pred_prefix_str = ''
        '''
        def jaccard_similarity(im1: np.ndarray, im2: np.ndarray):
            if im1.shape != im2.shape:
                message = f"Shape mismatch: input have different shape: im1: {im1.shape}, im2: {im2.shape}"
                logger.critical(message)
                raise ValueError(message)

            intersection = np.logical_and(im1, im2)
            union = np.logical_or(im1, im2)

            sum_union = float(union.sum())
            if sum_union == 0.0:
                # If 0 positive pixels in union: perfect prediction, so 1
                return 1
            else:
                sum_intersect = intersection.sum()
                return sum_intersect/sum_union
        '''

        # If there is a mask dir specified... use the groundtruth mask
        if input_mask_dir is not None and input_mask_dir.exists():
            # Read mask file and get all needed info from it...
            mask_filepath = Path(str(image_filepath).
                    replace(str(input_image_dir), str(input_mask_dir)))
                
            # Check if this file exists, if not, look for similar files
            if not mask_filepath.exists():
                files = list(mask_filepath.parent.glob(mask_filepath.stem + '*'))
                if len(files) == 1:
                    mask_filepath = files[0]
                else:
                    message = f"Error finding mask file with {mask_filepath.stem + '*'}: {len(files)} mask(s) found"
                    logger.error(message)
                    raise Exception(message)

            with rio.open(mask_filepath) as mask_ds:
                # Read pixels
                mask_arr = mask_ds.read(1)

            # Make the pixels at the borders of the mask black so they are 
            # ignored in the comparison
            if border_pixels_to_ignore and border_pixels_to_ignore > 0:
                mask_arr[0:border_pixels_to_ignore,:] = 0    # Left border
                mask_arr[-border_pixels_to_ignore:,:] = 0    # Right border
                mask_arr[:,0:border_pixels_to_ignore] = 0    # Top border
                mask_arr[:,-border_pixels_to_ignore:] = 0    # Bottom border
                
            # If there is more than 1 class, extract the seperate masks
            # per class with one-hot encoding
            if nb_classes > 1:
                mask_categorical_arr = tf.keras.utils.to_categorical(mask_arr, nb_classes, dtype=rio.uint8)
                mask_arr = (mask_categorical_arr[:,:,class_id]) * 255
                                
            #similarity = jaccard_similarity(mask_arr, image_pred)
            # Use accuracy as similarity... is more practical than jaccard
            similarity = np.array(np.equal(mask_arr, image_pred_uint8_cleaned_bin
                                 )).sum()/image_pred_uint8_cleaned_bin.size
            pred_prefix_str = f"{similarity:0.3f}_"
            
            # Write mask
            mask_copy_dest_filepath = (output_dir / 
                    f"{pred_prefix_str}{image_filepath.stem}_{class_name}_mask.tif")
            #if not mask_copy_dest_filepath.exists():     
            with rio.open(mask_copy_dest_filepath, 'w', driver='GTiff', 
                    compress='lzw',
                    height=mask_arr.shape[1], width=mask_arr.shape[0], 
                    count=1, dtype=rio.uint8, 
                    crs=image_crs, transform=image_transform) as dst:
                dst.write(mask_arr, 1)

        else:
            # If all_black, no need to calculate again
            if all_black: 
                pct_black = 1
            else:
                # Calculate percentage black pixels
                pct_black = 1 - ((image_pred_uint8_cleaned_bin.sum()/250)
                                    /image_pred_uint8_cleaned_bin.size)
            
            # If the result after segmentation is all black, set all_black
            if pct_black == 1:
                # Force the prefix to be really high so it is clear they are entirely black
                pred_prefix_str = "1.001_"
                all_black = True
            else:
                pred_prefix_str = f"{pct_black:0.3f}_"

            # If there are few white pixels, don't save it,
            # because we are in evaluetion mode anyway...
            #if similarity >= 0.95:
                #continue
        
        # Copy the input image if it doesn't exist yet in output path
        output_basefilepath = output_dir / f"{pred_prefix_str}{image_filepath.stem}{output_suffix}"
        image_dest_filepath = Path(str(output_basefilepath) + image_filepath.suffix)
        if not image_dest_filepath.exists():
            shutil.copyfile(image_filepath, image_dest_filepath)

        # Rename the prediction file so it also contains the prefix,... 
        if image_pred_filepath is not None:
            image_dest_filepath = Path(f"{str(output_basefilepath)}_pred{image_pred_filepath.suffix}")
            if not image_dest_filepath.exists():
                shutil.move(str(image_pred_filepath), image_dest_filepath)

        # If all_black, we are ready now
        if all_black:
            logger.debug("All black prediction, no use proceding")
            return 
        
        # Write a cleaned-up version for evaluation as well 
        #polygonize_pred_for_evaluation(
        #        image_pred_uint8_bin=image_pred_uint8_cleaned_bin,
        #        image_crs=image_crs,
        #        image_transform=image_transform,
        #        output_basefilepath=output_basefilepath)
    
    except Exception as ex:
        message = f"Exception postprocessing prediction for {image_filepath}\n: file {image_pred_filepath}!!!"
        raise Exception(message) from ex

def polygonize_pred_for_evaluation(
        image_pred_uint8_bin,
        image_crs: str,
        image_transform,
        output_basefilepath: Path):

    # Polygonize result
    try:
        # Returns a list of tupples with (geometry, value)
        polygonized_records = list(rio_features.shapes(
                image_pred_uint8_bin, mask=image_pred_uint8_bin, transform=image_transform))
        
        # If nothing found, we can return
        if len(polygonized_records) == 0:
            logger.debug("This prediction didn't result in any polygons")
            return

        # Convert shapes to geopandas geodataframe 
        geoms = []
        for geom, _ in polygonized_records:
            geoms.append(sh_geom.shape(geom))   
        geoms_gdf = gpd.GeoDataFrame(geoms, columns=['geometry'])
        geoms_gdf.crs = image_crs

        image_shape = image_pred_uint8_bin.shape
        image_width = image_shape[0]
        image_height = image_shape[1]

        # For easier evaluation, write the cleaned version as raster
        # Write the standard cleaned output to file
        logger.debug("Save binary prediction")
        image_pred_cleaned_filepath = Path(f"{str(output_basefilepath)}_pred_bin.tif")
        with rio.open(image_pred_cleaned_filepath, 'w', driver='GTiff', 
                        compress='lzw',
                        height=image_height, width=image_width, 
                        count=1, dtype=rio.uint8, 
                        crs=image_crs, transform=image_transform) as dst:
            dst.write(image_pred_uint8_bin, 1)
        
        # If the input image contained a tranform, also create an image 
        # based on the simplified vectors
        if(image_transform[0] != 0 
            and len(geoms) > 0):
            # Simplify geoms
            geoms_simpl = []
            geoms_simpl_vis = []
            for geom in geoms:
                # The simplify of shapely uses the deuter-pecker algo
                # preserve_topology is slower bu makes sure no polygons are removed
                geom_simpl = geom.simplify(0.5, preserve_topology=True)
                if not geom_simpl.is_empty:
                    geoms_simpl.append(geom_simpl)
            
            # Write simplified wkt result to raster for comparing. 
            if len(geoms_simpl) > 0:
                # TODO: doesn't support multiple classes
                logger.debug('Before writing simpl rasterized file')
                image_pred_simpl_filepath = f"{str(output_basefilepath)}_pred_cleaned_simpl.tif"
                with rio.open(image_pred_simpl_filepath, 'w', driver='GTiff', compress='lzw',
                                height=image_height, width=image_width, 
                                count=1, dtype=rio.uint8, crs=image_crs, transform=image_transform) as dst:
                    # this is where we create a generator of geom, value pairs to use in rasterizing
                    logger.debug('Before rasterize')
                    burned = rio_features.rasterize(
                            shapes=geoms_simpl, 
                            out_shape=(image_height, image_width),
                            fill=0, default_value=255, dtype=rio.uint8,
                            transform=image_transform)
                    dst.write(burned, 1)
            
            # Write simplified wkt result to raster for comparing. Use the same
            if len(geoms_simpl_vis) > 0:
                # file profile as created before for writing the raw prediction result
                # TODO: doesn't support multiple classes
                logger.debug('Before writing simpl with visvangali algo rasterized file')
                image_pred_simpl_filepath = f"{str(output_basefilepath)}_pred_cleaned_simpl_vis.tif"
                with rio.open(image_pred_simpl_filepath, 'w', driver='GTiff', compress='lzw',
                              height=image_height, width=image_width, 
                              count=1, dtype=rio.uint8, crs=image_crs, transform=image_transform) as dst:
                    # this is where we create a generator of geom, value pairs to use in rasterizing
                    logger.debug('Before rasterize')
                    burned = rio_features.rasterize(
                            shapes=geoms_simpl_vis, 
                            out_shape=(image_height, image_width),
                            fill=0, default_value=255, dtype=rio.uint8,
                            transform=image_transform)
                    dst.write(burned, 1)

    except Exception as ex:
        message = f"Exception while polygonizing to file {output_basefilepath}!"
        raise Exception(message) from ex

def polygonize_pred_from_file(
        image_pred_filepath: Path,
        border_pixels_to_ignore: int = 0,
        save_to_file: bool = False) -> Optional[gpd.GeoDataFrame]:

    try:
        with rio.open(image_pred_filepath) as image_ds:
            # Read geo info
            image_crs = image_ds.profile['crs']
            image_transform = image_ds.transform
            
            # Read pixels and change from (channels, width, height) to 
            # (width, height, channels) and normalize to values between 0 and 1
            image_data = image_ds.read()
    
         # Create binary version
        #image_data = rio_plot.reshape_as_image(image_data)
        image_pred_uint8_bin = to_binary_uint8(image_data, 125)

        output_basefilepath = None
        if save_to_file is True:
            output_basefilepath = image_pred_filepath.parent / image_pred_filepath.stem
        result_gdf = polygonize_pred(
                image_pred_uint8_bin=image_pred_uint8_bin,
                image_crs=image_crs,
                image_transform=image_transform,
                output_basefilepath=output_basefilepath,
                border_pixels_to_ignore=border_pixels_to_ignore)

        if result_gdf is None:
            logger.warn(f"Prediction didn't result in any polygons: {image_pred_filepath}")
            
        return result_gdf

    except Exception as ex:
        raise Exception(f"Error in polygonize_pred_from_file on {image_pred_filepath}") from ex

def polygonize_pred_multiclass_to_file(
        image_pred_arr: np.ndarray,
        image_crs: str,
        image_transform,
        min_pixelvalue_for_save,
        classes: list,
        output_vector_path: Path,
        prediction_cleanup_params: dict = None,
        border_pixels_to_ignore: int = 0) -> bool:

    # Polygonize the result...
    result_gdf = polygonize_pred_multiclass(
            image_pred_arr=image_pred_arr,
            image_crs=image_crs,
            image_transform=image_transform,
            min_pixelvalue_for_save=min_pixelvalue_for_save,
            classes=classes,
            prediction_cleanup_params=prediction_cleanup_params,
            border_pixels_to_ignore=border_pixels_to_ignore)

    # If there were polygons, save them...
    if result_gdf is not None:
        geofile.to_file(result_gdf, output_vector_path, append=True, index=False)
        return True
    else:
        return False

def polygonize_pred_multiclass(
        image_pred_arr: np.ndarray,
        image_crs: str,
        image_transform,
        min_pixelvalue_for_save,
        classes: list,
        prediction_cleanup_params: dict = None,
        border_pixels_to_ignore: int = 0) -> Optional[gpd.GeoDataFrame]:

    # Init
    result_gdf = None

    # Loop through channels and polygonize one by one...
    image_pred_shape = image_pred_arr.shape
    nb_channels = image_pred_shape[2]
    for channel_id in range(0, nb_channels):
        image_pred_curr_arr = image_pred_arr[:,:,channel_id]

        # Clean prediction
        image_pred_uint8_cleaned_curr = clean_prediction(
                image_pred_arr=image_pred_curr_arr, 
                border_pixels_to_ignore=border_pixels_to_ignore)
                        
        # If the cleaned result doesn't contain any useful values... go to next
        if(min_pixelvalue_for_save > 0 
           and not np.any(image_pred_uint8_cleaned_curr >= min_pixelvalue_for_save)):
            continue

        # Polygonize this channel 
        image_pred_uint8_bin = to_binary_uint8(image_pred_uint8_cleaned_curr, 125)
        result_channel_gdf = polygonize_pred(
                image_pred_uint8_bin=image_pred_uint8_bin,
                image_crs=image_crs,
                image_transform=image_transform,
                classname=classes[channel_id],
                prediction_cleanup_params=prediction_cleanup_params,
                border_pixels_to_ignore=border_pixels_to_ignore)

        # Add to result
        if result_channel_gdf is None:
            continue
        if result_gdf is None:
            # Check if the input has a crs
            if result_channel_gdf.crs is None:
                #geoms_file_gdf.crs = pyproj.CRS.from_user_input("EPSG:31370")
                raise Exception("STOP: input does not have a crs!") 
            result_gdf = result_channel_gdf
        else:
            result_gdf = gpd.GeoDataFrame(
                    pd.concat([result_gdf, result_channel_gdf], ignore_index=True), 
                    crs=result_gdf.crs)
    
    return result_gdf

def polygonize_pred(
        image_pred_uint8_bin,
        image_crs: str,
        image_transform,
        classname: str = None,
        output_basefilepath: Optional[Path] = None,
        prediction_cleanup_params: dict = None,
        border_pixels_to_ignore: int = 0) -> Optional[gpd.GeoDataFrame]:

    # Polygonize result
    try:
        # Returns a list of tupples with (geometry, value)
        polygonized_records = list(rio_features.shapes(
                image_pred_uint8_bin, mask=image_pred_uint8_bin, transform=image_transform))

        # If nothing found, we can return
        if len(polygonized_records) == 0:
            return None

        # Convert shapes to geopandas geodataframe 
        geoms = []
        for geom, _ in polygonized_records:
            geoms.append(sh_geom.shape(geom))   
        geoms_gdf = gpd.GeoDataFrame(geoms, columns=['geometry'])
        geoms_gdf.crs = image_crs

        # Calculate the bounds of the image in projected coordinates
        image_shape = image_pred_uint8_bin.shape
        image_width = image_shape[0]
        image_height = image_shape[1]
        image_bounds = rio_transform.array_bounds(
                image_height, image_width, image_transform)
        x_pixsize = get_pixelsize_x(image_transform)
        y_pixsize = get_pixelsize_y(image_transform)
        border_bounds = (image_bounds[0]+border_pixels_to_ignore*x_pixsize,
                         image_bounds[1]+border_pixels_to_ignore*y_pixsize,
                         image_bounds[2]-border_pixels_to_ignore*x_pixsize,
                         image_bounds[3]-border_pixels_to_ignore*y_pixsize)
        
        # Calculate the tolerance as half the diagonal of the square formed 
        # by the min pixel size, rounded up in centimeter
        if prediction_cleanup_params is not None:
            # If a simplify is asked... 
            if 'simplify_algorythm' in prediction_cleanup_params:
                # Define the bounds of the image as linestring, so points on this 
                # border are preserved during the simplify
                border_lines = sh_geom.LineString(sh_geom.box(*border_bounds).exterior.coords)
                geoms_gdf.geometry = geoms_gdf.geometry.apply(
                        lambda geom: gfo_vector_util.simplify_ext(
                                geometry=geom, 
                                algorythm=prediction_cleanup_params['simplify_algorythm'],
                                tolerance=prediction_cleanup_params['simplify_tolerance'], 
                                keep_points_on=border_lines))
                
                # Remove geom rows that became empty after simplify + explode
                geoms_gdf = geoms_gdf[~geoms_gdf.is_empty] 
                if len(geoms_gdf) == 0:
                    return None
                geoms_gdf = geoms_gdf[~geoms_gdf.isna()]  
                if len(geoms_gdf) == 0:
                    return None
                geoms_gdf = gpd.GeoDataFrame(geoms_gdf.reset_index(drop=True), crs=geoms_gdf.crs).explode()

        # Now we can calculate the "onborder" property
        geoms_gdf = vector_util.calc_onborder(geoms_gdf, border_bounds)

        # Add the classname if provided and area
        if classname is not None:
            geoms_gdf['classname'] = classname
        geoms_gdf['area'] = geoms_gdf.geometry.area

        # Write the geoms to file
        if output_basefilepath is not None:
            geom_filepath = Path(f"{str(output_basefilepath)}_pred_cleaned_2.geojson")
            geofile.to_file(geoms_gdf, geom_filepath, index=False)
        
        return geoms_gdf
            
    except Exception as ex:
        message = f"Exception while polygonizing to file {output_basefilepath}"
        raise Exception(message) from ex

def clean_and_save_prediction(
        image_image_filepath: Path,
        image_crs: str,
        image_transform: str,
        output_dir: Path,
        image_pred_arr: np.ndarray,
        classes: list,
        input_image_dir: Optional[Path] = None,
        input_mask_dir: Optional[Path] = None,
        border_pixels_to_ignore: int = 0,
        min_pixelvalue_for_save: int = 127,
        evaluate_mode: bool = False,
        force: bool = False) -> bool:

    # If nb. channels in prediction > 1, skip the first as it is the background
    image_pred_shape = image_pred_arr.shape
    nb_channels = image_pred_shape[2]
    if nb_channels > 1:
        channel_start = 1
    else:
        channel_start = 0

    for channel_id in range(channel_start, nb_channels):
        image_pred_curr_arr = image_pred_arr[:,:,channel_id]

        # Clean prediction
        image_pred_uint8_cleaned_curr = clean_prediction(
                image_pred_arr=image_pred_curr_arr, 
                border_pixels_to_ignore=border_pixels_to_ignore)
        
        # If the cleaned result contains useful values or in evaluate mode... save
        if(min_pixelvalue_for_save == 0 
           or np.any(image_pred_uint8_cleaned_curr >= min_pixelvalue_for_save)
           or evaluate_mode is True):

            # Find the class name in the classes list
            class_name = None
            for class_id, (classname) in enumerate(classes):
                if class_id == channel_id:
                    class_name = classname
                    break
            if class_name is None:
                raise Exception(f"No classname found for channel_id {channel_id}")
        
            # Now save prediction
            output_suffix=f"_{class_name}"
            image_pred_filepath = save_prediction_uint8(
                    image_filepath=image_image_filepath,
                    image_pred_uint8_cleaned=image_pred_uint8_cleaned_curr,
                    image_crs=image_crs,
                    image_transform=image_transform,
                    output_dir=output_dir,
                    output_suffix=output_suffix,
                    force=force)

            # Postprocess for evaluation
            if evaluate_mode is True:
                # Create binary version and postprocess
                image_pred_uint8_cleaned_bin = to_binary_uint8(image_pred_uint8_cleaned_curr, 125)                       
                postprocess_for_evaluation(
                        image_filepath=image_image_filepath,
                        image_crs=image_crs,
                        image_transform=image_transform,
                        image_pred_filepath=image_pred_filepath,
                        image_pred_uint8_cleaned_bin=image_pred_uint8_cleaned_bin,
                        output_dir=output_dir,
                        output_suffix=output_suffix,
                        input_image_dir=input_image_dir,
                        input_mask_dir=input_mask_dir,
                        class_id=channel_id,
                        class_name=class_name,
                        nb_classes=nb_channels,
                        border_pixels_to_ignore=border_pixels_to_ignore,
                        force=force)

    return True

def clean_prediction(
        image_pred_arr: np.ndarray,
        border_pixels_to_ignore: int = 0,
        output_color_depth: str = 'binary') -> np.ndarray:
    """
    Cleans a prediction result and returns a cleaned, uint8 array.
    
    Args:
        image_pred_arr (np.array): The prediction as returned by keras.
        border_pixels_to_ignore (int, optional): Border pixels to ignore. Defaults to 0.
        output_color_depth (str, optional): Color depth desired. Defaults to '2'.
            * binary: 0 or 255
            * full: 256 different values
    
    Returns:
        np.array: The cleaned result.
    """

    # Input should be float32
    if image_pred_arr.dtype not in [np.float32, np.uint8]:
        raise Exception(f"image prediction is in an unsupported type: {image_pred_arr.dtype}") 
    if output_color_depth not in ['binary', 'full']:
        raise Exception(f"Unsupported output_color_depth: {output_color_depth}")

    # Reshape from 3 to 2 dims if necessary (width, height, nb_channels).
    # Check the number of channels of the output prediction
    image_pred_shape = image_pred_arr.shape
    if len(image_pred_shape) > 2:
        n_channels = image_pred_shape[2]
        if n_channels > 1:
            raise Exception("Invalid input, should be one channel!")
        # Reshape array from 3 dims (width, height, nb_channels) to 2.
        image_pred_uint8 = np.reshape(image_pred_arr, (image_pred_shape[0], image_pred_shape[1]))   

    # Convert to uint8 if necessary
    if image_pred_arr.dtype == np.float32:
        image_pred_uint8 = np.array((image_pred_arr * 255), dtype=np.uint8)
    else:
        image_pred_uint8 = image_pred_arr

    # Convert to binary if needed
    if output_color_depth == 'binary':
        image_pred_uint8[image_pred_uint8 >= 127] = 255
        image_pred_uint8[image_pred_uint8 < 127] = 0
    
    # Make the pixels at the borders of the prediction black so they are ignored
    image_pred_uint8_cropped = image_pred_uint8
    if border_pixels_to_ignore and border_pixels_to_ignore > 0:
        image_pred_uint8_cropped[0:border_pixels_to_ignore,:] = 0    # Left border
        image_pred_uint8_cropped[-border_pixels_to_ignore:,:] = 0    # Right border
        image_pred_uint8_cropped[:,0:border_pixels_to_ignore] = 0    # Top border
        image_pred_uint8_cropped[:,-border_pixels_to_ignore:] = 0    # Bottom border
    
    return image_pred_uint8_cropped

def save_prediction_uint8(
        image_filepath: Path,
        image_pred_uint8_cleaned: np.ndarray,
        image_crs: str,
        image_transform: str,
        output_dir: Path,
        output_suffix: str = '',
        border_pixels_to_ignore: int = None,
        force: bool = False) -> Path:

    ##### Init #####
    # If no decent transform metadata, stop!
    if image_transform is None or image_transform[0] == 0:
        message = f"No transform found for {image_filepath}: {image_transform}"
        logger.error(message)
        raise Exception(message)

    # Make sure the output dir exists...
    if not output_dir.exists():
        output_dir.mkdir()
    
    # Write prediction to file
    output_filepath = output_dir / f"{image_filepath.stem}{output_suffix}_pred.tif"
    logger.debug("Save +- original prediction")
    image_shape = image_pred_uint8_cleaned.shape
    image_width = image_shape[0]
    image_height = image_shape[1]
    with rio.open(str(output_filepath), 'w', driver='GTiff', tiled='no',
                  compress='lzw', predictor=2, num_threads=4,
                  height=image_height, width=image_width, 
                  count=1, dtype=rio.uint8, crs=image_crs, transform=image_transform) as dst:
        dst.write(image_pred_uint8_cleaned, 1)
    
    return output_filepath

#-------------------------------------------------------------
# Helpers for working with Affine objects...                    
#-------------------------------------------------------------

def get_pixelsize_x(transform):
    return transform[0]
    
def get_pixelsize_y(transform):
    return -transform[4]

#-------------------------------------------------------------
# If the script is ran directly...
#-------------------------------------------------------------

if __name__ == '__main__':
    raise Exception("Not implemented")
