#include<bits/stdc++.h>
using namespace std;

string aseertSynthesier1(string condition);
string aseertSynthesier2(string condition);

int main(int argc,char* args[])
{
    ifstream fin;
    ofstream fout;
   // string a=strcat(args[1],"");
   //string inputFileName=a+".sol";
    //string outputFileName=a+"_mod.sol";
    string inputFileName=args[1];
    string outputFileName=inputFileName+".temp";
    int condition_count=0;
    
    fin.open(inputFileName);
    fout.open(outputFileName);
   //fout<<"//SPDX-License-Identifier: UNLICENSED\n";
    //fout<<"pragma solidity ^0.8.16;\n";
    fout<<"pragma solidity >=0.4.24;\n";
    string codePerLine;
    while (getline(fin,codePerLine))
    {
        vector <string> conditions;
        string firstWord="";
        int pos=0;
        while(codePerLine[pos]==' '||codePerLine[pos]=='\t')
        {
            pos++;
        }
         if(codePerLine[pos]=='\0')
         {
           continue;
         }
        while(codePerLine[pos]!='\0'&&codePerLine[pos]!=' '&&codePerLine[pos]!='(')
        {
            firstWord=firstWord+codePerLine[pos];
            pos++;
        }
        if(firstWord=="pragma")
        {
            continue;
        }
        else if(firstWord=="for")
        {
           string temp_condition="";
            while(codePerLine[pos]!=';')
            {
                pos++;
            }
                pos++;
            while(codePerLine[pos]!=';')
           {
               if(codePerLine[pos]=='|' && codePerLine[pos+1]=='|')
                {
                    conditions.push_back(temp_condition);
                    temp_condition="";
                    pos=pos+2;
                }
                else if(codePerLine[pos]=='&' && codePerLine[pos+1]=='&')
                {
                    conditions.push_back(temp_condition);
                    temp_condition="";
                    pos=pos+2;
                }
                else if(codePerLine[pos]==',')
                {
                    conditions.push_back(temp_condition);
                    temp_condition="";
                    pos=pos+1;  
                }
                else
                {
                    temp_condition=temp_condition+codePerLine[pos];
                    pos++;
                }
           }
            conditions.push_back(temp_condition);
            condition_count=condition_count+conditions.size();
            while(codePerLine[pos]!='{')
            {
                if(codePerLine[pos]=='\0')
                {
                    fout<<codePerLine<<endl;
                    getline(fin,codePerLine);
                    pos=0;
                }
                pos++;
            }
            fout<<codePerLine<<endl;
            for(int i=0;i<conditions.size();i++)
            {
                fout<<aseertSynthesier1(conditions[i])<<endl;
                fout<<aseertSynthesier2(conditions[i])<<endl;
            }
            conditions.clear();
        }
        else if(firstWord=="assert")
        {
            string temp="\trequire";
            while(codePerLine[pos]!='\0')
            {
                temp=temp+codePerLine[pos];
                pos++;
            }
            fout<<temp<<endl;
        }
        else if(firstWord=="if")
        {

            string temp_condition="";
            int openbracket=0;
            vector <string> tempCodePerLine;
            while(codePerLine[pos]==' ')
            {
                pos++;
            }
            pos++;
            while(codePerLine[pos]!=')' || openbracket !=0 )
            {
                if(codePerLine[pos]=='\0')
                {
                    tempCodePerLine.push_back(codePerLine);
                    pos=0;
                    getline(fin,codePerLine);
                }
                else if(codePerLine[pos]=='|' && codePerLine[pos+1]=='|')
                {
                    conditions.push_back(temp_condition);
                    temp_condition="";
                    pos=pos+2;
                }
                else if(codePerLine[pos]=='&' && codePerLine[pos+1]=='&')
                {
                    conditions.push_back(temp_condition);
                    temp_condition="";
                    pos=pos+2;
                }
                else
                {
                    temp_condition=temp_condition+codePerLine[pos];
                    if(codePerLine[pos]=='(')
                        openbracket++;
                    else if(codePerLine[pos]==')')
                        openbracket--;
                    pos++;
                }
            }
            conditions.push_back(temp_condition);
            condition_count=condition_count+conditions.size();
            for(int i=0;i<conditions.size();i++)
            {
                fout<<aseertSynthesier1(conditions[i])<<endl;
                fout<<aseertSynthesier2(conditions[i])<<endl;
            }
            if(tempCodePerLine.size()>0)
            {
                for(int i=0;i<tempCodePerLine.size();i++)
                {
                    fout<<tempCodePerLine[i]<<endl;
                }
            }
            fout<<codePerLine<<endl;
            conditions.clear();
            tempCodePerLine.clear();
        }
        else if(firstWord=="require")
        {
            string temp_condition="";
            int openbracket=0;
            vector <string> tempCodePerLine;
            while(codePerLine[pos]==' ')
            {
                pos++;
            }
            pos++; 
            while( (codePerLine[pos]!=')' && codePerLine[pos]!=',') || openbracket != 0 || openbracket > 0)
            {
               if(codePerLine[pos]=='\0')
                {
                    tempCodePerLine.push_back(codePerLine);
                    pos=0;
                    getline(fin,codePerLine);
                }
                else if(codePerLine[pos]=='|' && codePerLine[pos+1]=='|')
                {
                    conditions.push_back(temp_condition);
                    temp_condition="";
                    pos=pos+2;
                }
                else if(codePerLine[pos]=='&' && codePerLine[pos+1]=='&')
                {
                    conditions.push_back(temp_condition);
                    temp_condition="";
                    pos=pos+2;
                }
                else
                {
                    temp_condition=temp_condition+codePerLine[pos];
                     if(codePerLine[pos]=='(')
                        openbracket++;
                    else if(codePerLine[pos]==')')
                        openbracket--;
                    pos++;

                }
            }
            conditions.push_back(temp_condition);
            condition_count=condition_count+conditions.size();
            for(int i=0;i<conditions.size();i++)
            {
                fout<<aseertSynthesier1(conditions[i])<<endl;
                fout<<aseertSynthesier2(conditions[i])<<endl;
            }
            if(tempCodePerLine.size()>0)
            {
                for(int i=0;i<tempCodePerLine.size();i++)
                {
                    fout<<tempCodePerLine[i]<<endl;
                }
            }
            fout<<codePerLine<<endl;
            conditions.clear();
            tempCodePerLine.clear(); 
        }
        else if(firstWord=="while")
        {
            string temp_condition="";
            int openbracket=0;
            while(codePerLine[pos]!='(')
            {
                pos++;
            }  
            pos++;
            while(codePerLine[pos]!=')' || openbracket !=0 )
            {
                if(codePerLine[pos]=='\0')
                {
                    fout<<codePerLine<<endl;
                    pos=0;
                    getline(fin,codePerLine);
                }
                else if(codePerLine[pos]=='|' && codePerLine[pos+1]=='|')
                {
                    conditions.push_back(temp_condition);
                    temp_condition="";
                    pos=pos+2;
                }
                else if(codePerLine[pos]=='&' && codePerLine[pos+1]=='&')
                {
                    conditions.push_back(temp_condition);
                    temp_condition="";
                    pos=pos+2;
                }
                else
                {
                    temp_condition=temp_condition+codePerLine[pos];
                    if(codePerLine[pos]=='(')
                        openbracket++;
                    else if(codePerLine[pos]==')')
                        openbracket--;
                    pos++;
                }
            }
            conditions.push_back(temp_condition);
            condition_count=condition_count+conditions.size();
            while(codePerLine[pos]!='{')
            {
                if(codePerLine[pos]=='\0')
                {
                    fout<<codePerLine<<endl;
                    getline(fin,codePerLine);
                    pos=0;
                }
                pos++;
            }
            fout<<codePerLine<<endl;
            for(int i=0;i<conditions.size();i++)
            {
                fout<<aseertSynthesier1(conditions[i])<<endl;
                fout<<aseertSynthesier2(conditions[i])<<endl;
            }
            conditions.clear();

        }
        else
        {
            fout<<codePerLine<<endl;
        }
    }

    cout<<condition_count*2<<endl;
    fin.close();
    fout.close();
    remove(inputFileName.c_str());
    rename(outputFileName.c_str(), inputFileName.c_str());
    return 0;

}

/////////////////////////////////////////////////////////////////////////////////////

string aseertSynthesier1(string condition)
{
    string assertion="\tassert(!("+condition+"));";
    return assertion;
}
string aseertSynthesier2(string condition)
{
    string assertion="\tassert(!(!("+condition+")));";
    return assertion;
}
