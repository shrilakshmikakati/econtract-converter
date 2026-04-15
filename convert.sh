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

new_file=${1%.txt}
name=$(basename "$1")
name="${name%.*}"
#python3 cli.py run --file $1
python3 econtract_converter.py $1



if [ ! -d ./$RESULT_DIR/$name-$2 ];
then
mkdir ./$RESULT_DIR/$name-$2
fi 
chg_fle=$pwd

if [ ! -d ./$RESULT_DIR/$name-$2/Assert ];
then
mkdir ./$RESULT_DIR/$name-$2/Assert
fi 


mv ./Results/$name/* ./$RESULT_DIR/$name-$2


rmdir ./Results/$name
SOL_FILE=$(find "./$RESULT_DIR/$name-$2" -name "*.sol" | head -n 1)
 echo "$SOL_FILE"
cp $SOL_FILE ./$RESULT_DIR/$name-$2/Assert


#OUTPUT_FILE="$RESULT_DIR/$new_file-$2/$new_file.sol"
resultFile="${name}_output.txt"
outputfile="${name}_Final_Output.txt"; 

# Move generated file into result folder
if [ ! -f "$SOL_FILE" ]; then
    echo "Error: Smart contract was not generated!"
    exit 1
fi
assertionInsertCount=`./.assertinserter ./$RESULT_DIR/$name-$2/Assert/${new_file}.sol`


if [ "$2" == "bmc" ]
then
#sol_comp=$( solc "$SOL_FILE" --model-checker-engine bmc --model-checker-targets assert  &> ./$RESULT_DIR/$name-$2/$resultFile )	
sol_comp=$( solc "./$RESULT_DIR/$name-$2/Assert/${new_file}.sol" --model-checker-engine bmc --model-checker-targets assert  &> ./$RESULT_DIR/$name-$2/Assert/$resultFile )	
sed -i 's/Warning: BMC:/CheckPoint\nWarning: BMC:/g' ./$RESULT_DIR/$name-$2/Assert/$resultFile 

sed -n '/Warning: BMC: Assertion violation happens here./, /CheckPoint/p' ./$RESULT_DIR/$name-$2/Assert/$resultFile &> ./$RESULT_DIR/$name-$2/Assert/$outputfile 
fi
   
if [ "$2" == "chc" ]
then
sol_comp=$( solc "./$RESULT_DIR/$name-$2/Assert/${new_file}.sol" --model-checker-engine chc --model-checker-targets assert  &> ./$RESULT_DIR/$name-$2/Assert/$resultFile )	
sed -i 's/Warning: CHC:/CheckPoint\nWarning: CHC:/g' ./$RESULT_DIR/$name-$2/Ass# ── Banner ─────────────────────────────────────────────────────────────────
BANNER = """
╔══════════════════════════════════════════════════════════════╗
║          E-Contract → Solidity Converter  v3.0              ║
║     generate → solc compile → validate → repair loop        ║
╚══════════════════════════════════════════════════════════════╝"""ert/$resultFile 

sed -n '/Warning: CHC: Assertion violation happens here./, /CheckPoint/p' ./$RESULT_DIR/$name-$2/Assert/$resultFile &> ./$RESULT_DIR/$name-$2/Assert/$outputfile 
fi


grep "assert(" ./$RESULT_DIR/$name-$2/Assert/$outputfile > ./$RESULT_DIR/$name-$2/Assert/.grep_result.txt

cut -d "|" -f 1 ./$RESULT_DIR/$name-$2/Assert/.grep_result.txt > ./$RESULT_DIR/$name-$2/Assert/.cut_result.txt 
sort -n -u ./$RESULT_DIR/$name-$2/Assert/.cut_result.txt  > ./$RESULT_DIR/$name-$2/Assert/.sort_result.txt
sort -n  ./$RESULT_DIR/$name-$2/Assert/.grep_result.txt > ./$RESULT_DIR/$name-$2/Assert/Dynamic_Assertions.txt
sort -n -u ./$RESULT_DIR/$name-$2/Assert/Dynamic_Assertions.txt > ./$RESULT_DIR/$name-$2/Assert/Unique_Assertions.txt
grep "assert" ./$RESULT_DIR/$name-$2/Assert/$name.sol  > ./$RESULT_DIR/$name-$2/Assert/Assertions_Insertesd.txt
dynamic=`wc -l < ./$RESULT_DIR/$name-$2/Assert/.cut_result.txt`
uniq=`wc -l < ./$RESULT_DIR/$name-$2/Assert/.sort_result.txt` 

if [[ $assertionInsertCount -gt 0 ]]; then
let atomiccondition=$assertionInsertCount/2
conditioncoverage=$(($uniq*100/$assertionInsertCount))
else
conditioncoverage=0
fi
echo "Properties inserted : ${assertionInsertCount}"
echo "Properties violation detected (dynamic) : ${dynamic}"
echo "Properties violation detected (unique) : ${uniq}"
echo "Total atomic condition : ${atomiccondition}"
echo "Condition Coverage % : ${conditioncoverage}%"
{
echo "Properties inserted : ${assertionInsertCount}" 
echo "Properties violation detected (dynamic) : ${dynamic}" 
echo "Properties violation detected (unique) : ${uniq}" 
echo "Total atomic condition : ${atomiccondition}"
echo "Condition Coverage % : ${conditioncoverage}%" 
} > $name-result.txt
mv  $name-result.txt Results/$name-$2/Assert/

finalOutput="${new_file}_result.txt"
rm ./$RESULT_DIR/$name-$2/Assert/.grep_result.txt
rm ./$RESULT_DIR/$name-$2/Assert/.cut_result.txt
rm ./$RESULT_DIR/$name-$2/Assert/.sort_result.txt