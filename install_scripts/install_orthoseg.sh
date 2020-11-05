#!/bin/bash

# If no parameters passed, show help...
if [ -z "$var" ]
then
  echo
  echo Hello! If you want to override some default options this is possible as such:
  echo 'install_orthoseg.sh --envname orthosegdev --envname_backup orthosegdev_bck_2020-01-01 --condadir "/home/Miniconda3" --fordev Y'
  echo 
  echo The parameters can be used as such:
  echo     - envname: the name the new environment will be given 
  echo     - envname_backup: if the environment already exist, it will be 
  echo       backupped to this environment
  echo     - condadir: the directory where cona is installed
  echo     - fordev: for development: if Y is passed, only the dependencies 
  echo       for orthoseg will be installed, not the orthoseg package itself
fi

# Extract the parameters passed
while [[ "$#" -gt 0 ]]; do
    case $1 in
        -e|--envname) envname="$2"; shift ;;
        -cd|--condadir) condadir="$2"; shift ;;
        -eb|--envname_backup) envname_backup="$2"; shift ;;
        -od|--fordev) fordev="$2"; shift ;;
        *) echo "Unknown parameter passed: $1"; exit 1 ;;
    esac
    shift
done

# Format current date
today=$(date +%F)

# If not provided, init parameters with default values
if [ -z "$envname" ]; then envname="orthoseg" ; fi
if [ -z "$envname_backup" ]; then envname_backup="${envname}_bck_${today}" ; fi
if [ -z "$condadir" ]; then condadir="$HOME/Miniconda3" ; fi
if [ -z "$fordev" ]; then fordev="N" ; fi

# If no parameters are given, ask if it is ok to use defaults
echo
echo "The script will be ran with the following parameters:"
echo "   - envname=$envname"
echo "   - envname_backup=$envname_backup"
echo "   - condadir=$condadir"
echo "   - fordev=$fordev"
echo

read -p "Do you want to move on with these choices? (y/n)" -n 1 -r
echo    
if [[ ! $REPLY =~ ^[Yy]$ ]]
then
    [[ "$0" = "$BASH_SOURCE" ]] && exit 1 || return 1 # handle exits from shell or function but don't exit interactive shell
fi

# Init conda
. "$condadir/etc/profile.d/conda.sh"

#-------------------------------------
# RUN!
#-------------------------------------
echo
echo Backup existing environment
echo -------------------------------------

if [[ ! -z "$envname_backup" ]]
then
  if [[ -d "$condadir/envs/$envname/" ]]
  then
    echo "Do you want to take a backup from $envname?"
    if [[ -d "$condadir/envs/$envname_backup/" ]]
    then
      echo "REMARK: $envname_backup exists already, so will be overwritten by a new backup!"
    fi
    
    read -p "y=take backup, n=don't take backup but proceed, c=stop script (y/n/c)" -n 1 -r
    echo    
    if [[ $REPLY =~ ^[Cc]$ ]]
    then
        [[ "$0" = "$BASH_SOURCE" ]] && exit 1 || return 1 # handle exits from shell or function but don't exit interactive shell
    elif [[ $REPLY =~ ^[Yy]$ ]]
    then  
      conda create --name "$envname_backup" --clone "$envname"
    fi
  else 
    echo "No existing environment $envname found to backup"
  fi
fi

echo
echo Create/overwrite environment
echo -------------------------------------
conda create -y --name $envname
conda activate $envname
conda config --env --add channels conda-forge
conda config --env --set channel_priority strict
conda install -y python=3.8 pillow rasterio geopandas=0.8.1 pygeos owslib cudatoolkit=10.1 cudnn hdf5=1.10.5

# For the following packages, no conda package is available or -for tensorflow- no recent version.
if [[ ! $fordev =~ ^[Yy]$ ]]
then
  pip install orthoseg
else
  echo
  echo Prepare for development: only install dependencies + install dev tools
  echo
  conda install -y pylint pytest rope
  pip install "tensorflow>=2.2,<=2.3" "segmentation-models>=1.0,<1.1" "geofileops==0.1.0" 
fi

# Deactivate new env + base
conda deactivate
conda deactivate

# Pause
read -s -n 1 -p "Press any key to continue . . ."
