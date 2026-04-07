#!/bin/bash
# ---------------------------------------
# E-Contract → Solidity Converter
# Output goes into result/ folder
# ---------------------------------------
echo "========================================"
echo " E-Contract → Solidity Converter"
echo "========================================"
# Check input argument
if [ -z "$1" ]; then
    echo "Usage: ./convert.sh <econtract.txt>"
    exit 1
fi

if [ ! "$2" ==  "bmc"  ] && [ ! "$2" ==  "chc"  ] ; 
then
echo " $2 Error: Please add 'bmc' or 'chc' checker"
exit
fi


INPUT_FILE=$1
RESULT_DIR="Results"
# Check if file exists
if [ ! -f "$INPUT_FILE" ]; then
    echo "Error: File '$INPUT_FILE' not found!"
    exit 1
fi

# Create result folder if not exists
if [ ! -d "$RESULT_DIR" ]; then
    mkdir "$RESULT_DIR"
    echo "Created result directory."
fi

#new_file=${1%.txt}
name=$(basename "$1")
name="${name%.*}"
python3 econtract_converter.py $1


if [ ! -d ./$RESULT_DIR/$name-$2 ];
then
mkdir ./$RESULT_DIR/$name-$2
fi 
chg_fle=$pwd


mv ./Results/$name/* ./$RESULT_DIR/$name-$2
rmdir ./Results/$name
SOL_FILE=$(find "./$RESULT_DIR/$name-$2" -name "*.sol" | head -n 1)
 echo "$SOL_FILE"


#cp "$1" "./$RESULT_DIR/$new_file-$2"
#OUTPUT_FILE="$RESULT_DIR/$new_file-$2/GeneratedContract.sol"
OUTPUT_FILE="$RESULT_DIR/$new_file-$2/$new_file.sol"
resultFile="${name}_output.txt"
outputfile="${name}_Final_Output.txt"; 

# Move generated file into result folder
if [ ! -f "$SOL_FILE" ]; then
    echo "Error: Smart contract was not generated!"
    exit 1
fi
 

if [ "$2" == "bmc" ]
echo "$2"
then
sol_comp=$( solc "$SOL_FILE" --model-checker-engine bmc --model-checker-targets assert  &> ./$RESULT_DIR/$name-$2/$resultFile )	
sed -i 's/Warning: BMC:/CheckPoint\nWarning: BMC:/g' ./$RESULT_DIR/$name-$2/$resultFile 

sed -n '/Warning: BMC: Assertion violation happens here./, /CheckPoint/p' ./$RESULT_DIR/$name-$2/$resultFile &> ./$RESULT_DIR/$name-$2/$outputfile 
fi
   
if [ "$2" == "chc" ]
then
sol_comp=$( solc "$SOL_FILE" --model-checker-engine chc --model-checker-targets assert  &> ./$RESULT_DIR/$name-$2/$resultFile )	
sed -i 's/Warning: CHC:/CheckPoint\nWarning: CHC:/g' ./$RESULT_DIR/$name-$2/$resultFile 

sed -n '/Warning: CHC: Assertion violation happens here./, /CheckPoint/p' ./$RESULT_DIR/$name-$2/$resultFile &> ./$RESULT_DIR/$name-$2/$outputfile 
fi
# Optional: Compile using solc if installed
#if command -v solc &> /dev/null; then
  #  echo "Compiling with solc..."
   # solc --bin --abi "$OUTPUT_FILE" -o "$RESULT_DIR"
    #echo "Compilation output saved inside result/ folder."
#else
 #   echo "solc not installed. Skipping compilation."
#fi
grep "assert(" ./$RESULT_DIR/$name-$2/$outputfile > ./$RESULT_DIR/$name-$2/.grep_result.txt

cut -d "|" -f 1 ./$RESULT_DIR/$name-$2/.grep_result.txt > ./$RESULT_DIR/$name-$2/.cut_result.txt 
sort -n -u ./$RESULT_DIR/$name-$2/.cut_result.txt  > ./$RESULT_DIR/$name-$2/.sort_result.txt
sort -n  ./$RESULT_DIR/$name-$2/.grep_result.txt > ./$RESULT_DIR/$name-$2/Dynamic_Assertions.txt
sort -n -u ./$RESULT_DIR/$name-$2/Dynamic_Assertions.txt > ./$RESULT_DIR/$name-$2/Unique_Assertions.txt
grep "assert" ./$RESULT_DIR/$name-$2/$name.sol  > ./$RESULT_DIR/$name-$2/Assertions_Insertesd.txt
dynamic=`wc -l < ./$RESULT_DIR/$name-$2/.cut_result.txt`
uniq=`wc -l < ./$RESULT_DIR/$name-$2/.sort_result.txt` 
let atomiccondition=$assertionInsertCount/2
if [[ $atomiccondition -gt 0 ]]; then
conditioncoverage=$(($uniq*100/$assertionInsertCount))
else
conditioncoverage=0
fi
echo "Properties inserted : ${assertionInsertCount}"
echo "Properties violation detected (dynamic) : ${dynamic}"
echo "Properties violation detected (unique) : ${uniq}"
echo "Total atomic condition : ${atomiccondition}"
echo "Condition Coverage % : ${conditioncoverage}%"

finalOutput="${new_file}_result.txt"
rm ./$RESULT_DIR/$name-$2/.grep_result.txt
rm ./$RESULT_DIR/$name-$2/.cut_result.txt
rm ./$RESULT_DIR/$name-$2/.sort_result.txt