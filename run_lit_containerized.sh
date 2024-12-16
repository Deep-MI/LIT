#!/bin/bash

set -e

function usage()
{
cat << EOF

Usage: run_lit_docker.sh --input_image <input_t1w_volume> --mask_image <lesion_mask_volume> --output_directory <output_directory>  [OPTIONS]

run_lit_docker.sh takes a T1 full head image and creates:
     (i)  an inpainted T1w image using a lesion mask
     (ii) (optional) whole brain segmentation and cortical surface reconstruction using FastSurferVINN

FLAGS:
  -h, --help
      Print this message and exit
  --gpus <gpus>
      GPUs to use. Default: all
  -i, --input_image <input_image>
      Path to the input T1w volume
  -m, --mask_image <mask_image>
      Path to the lesion mask volume (same dimensions as input_image, >0 for lesion, 0 for background)
  -o, --output_directory <output_directory>
      Path to the output directory

Examples:
  ./run_lit_docker.sh -i t1w.nii.gz -m lesion.nii.gz -o ./output
  ./run_lit_docker.sh -i t1w.nii.gz -m lesion.nii.gz -o ./output --fastsurfer --gpus 0



REFERENCES:

If you use this for research publications, please cite:

EOF
}

# Validate required parameters
if [[ $# -eq 0 ]]; then
  usage
  exit 1
fi

POSITIONAL_ARGS=()

# Initialize RUN_FASTSURFER to false by default
RUN_FASTSURFER=false

# Initialize USE_SINGULARITY to false by default
USE_SINGULARITY=false

while [[ $# -gt 0 ]]; do
  case $1 in
    --gpus)
        GPUS="$2"
        shift # past argument
        shift # past value
        ;;
    -i|--input_image)
      INPUT_IMAGE="$2"
      shift # past argument
      shift # past value
      ;;
    -m|--mask_image)
      MASK_IMAGE="$2"
      shift # past argument
      shift # past value
      ;;
    -o|--output_directory)
      OUT_DIR="$2"
      shift # past argument
      shift # past value
      ;;
    --fastsurfer)
      RUN_FASTSURFER=true
      shift # past value
      ;;
    -h|--help)
      usage
      exit
      ;;
    --fs_license)
      fs_license="$2"
      shift # past argument
      shift # past value
      ;;
    --use_singularity)
      USE_SINGULARITY=true
      shift # past value
      ;;
    *)
      POSITIONAL_ARGS+=("$1") # save positional arg
      shift # past argument
      ;;
  esac
done

set -- "${POSITIONAL_ARGS[@]}"

# Validate required parameters and files
if [ -z "$INPUT_IMAGE" ] || [ -z "$MASK_IMAGE" ] || [ -z "$OUT_DIR" ]; then
  echo "Error: input_image, mask_image, and output_directory are required parameters"
  usage
  exit 1
fi

if [ ! -f "$INPUT_IMAGE" ]; then
  echo "Error: Input image not found: $INPUT_IMAGE"
  exit 1
fi

if [ ! -f "$MASK_IMAGE" ]; then
  echo "Error: Mask image not found: $MASK_IMAGE"
  exit 1
fi

if [ "$USE_SINGULARITY" = true ]; then
  if [ ! -f "containerization/deepmi_lit_dev.simg" ]; then
    echo "Error: Singularity image not found: containerization/deepmi_lit_dev.simg"
    exit 1
  fi
fi

mkdir -p "$OUT_DIR"

# Make all inputs absolute paths
INPUT_IMAGE=$(realpath "$INPUT_IMAGE")
MASK_IMAGE=$(realpath "$MASK_IMAGE")
OUT_DIR=$(realpath "$OUT_DIR")

if [ -z "$GPUS" ]; then
  GPUS="all"
fi

fs_license=""

# try to find license file, using default locations
if [ "$RUN_FASTSURFER" = true ]; then
  if [ -z "$fs_license" ]; then
    for license_path in \
      "/fs_license/license.txt" \
      "$FREESURFER_HOME/license.txt" \
      "$FREESURFER_HOME/.license"; do
      if [ -f "$license_path" ]; then
        fs_license="$license_path"
        break
      fi
    done
    if [ -z "$fs_license" ]; then
      echo "Error: FreeSurfer license file not found"
      exit 1
    fi
  fi
  POSITIONAL_ARGS+=("--fastsurfer")
else
  fs_license=/dev/null
fi

# Run command based on the containerization tool
if [ "$USE_SINGULARITY" = true ]; then
  if [ ! -f "containerization/deepmi_lit.simg" ]; then
    wget https://github.com/Deep-MI/LIT/releases/download/v0.5.0/deepmi_lit.simg -O containerization/deepmi_lit.simg
  fi


  singularity exec --nv \
    -B "${INPUT_IMAGE}":"${INPUT_IMAGE}":ro \
    -B "${MASK_IMAGE}":"${MASK_IMAGE}":ro \
    -B "${OUT_DIR}":"${OUT_DIR}" \
    -B "$(pwd)":/workspace \
    -B "${fs_license:-/dev/null}":/fs_license/license.txt:ro \
    ./containerization/deepmi_lit.simg \
    deepmi/lit -i "${INPUT_IMAGE}" -m "${MASK_IMAGE}" -o "${OUT_DIR}" "${POSITIONAL_ARGS[@]}"
else
  docker run --gpus "device=$GPUS" -it --ipc=host \
    --ulimit memlock=-1 --ulimit stack=67108864 --rm \
    -v "${INPUT_IMAGE}":"${INPUT_IMAGE}":ro \
    -v "${MASK_IMAGE}":"${MASK_IMAGE}":ro \
    -v "${OUT_DIR}":"${OUT_DIR}" \
    -u "$(id -u):$(id -g)" \
    -v "$(pwd)":/workspace \
    -v "${fs_license:-/dev/null}":/fs_license/license.txt:ro \
    deepmi/lit:dev -i "${INPUT_IMAGE}" -m "${MASK_IMAGE}" -o "${OUT_DIR}" "${POSITIONAL_ARGS[@]}"
fi