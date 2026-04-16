#include<bits/stdc++.h>
using namespace std;

// assert(cond)  => checks the condition can be TRUE
// assert(!cond) => checks the condition can be FALSE
string assertSynthesizer1(string condition);
string assertSynthesizer2(string condition);

// Trim leading/trailing spaces from a condition string
string trim(const string& s) {
    int start = 0, end = (int)s.size() - 1;
    while (start <= end && (s[start] == ' ' || s[start] == '\t')) start++;
    while (end >= start && (s[end] == ' ' || s[end] == '\t')) end--;
    return s.substr(start, end - start + 1);
}

int main(int argc, char* args[])
{
    ifstream fin;
    ofstream fout;

    string inputFileName  = args[1];
    string outputFileName = inputFileName + ".temp";
    int condition_count   = 0;

    fin.open(inputFileName);
    fout.open(outputFileName);

    fout << "pragma solidity >=0.4.24;\n";

    string codePerLine;
    while (getline(fin, codePerLine))
    {
        vector<string> conditions;
        string firstWord = "";
        int pos = 0;

        // Skip leading whitespace
        while (codePerLine[pos] == ' ' || codePerLine[pos] == '\t')
            pos++;

        // Skip blank lines
        if (codePerLine[pos] == '\0')
            continue;

        // Extract first word (stops at space, '(' or end)
        while (codePerLine[pos] != '\0' && codePerLine[pos] != ' ' && codePerLine[pos] != '(')
        {
            firstWord = firstWord + codePerLine[pos];
            pos++;
        }

        // ── pragma: skip (we already wrote our own) ──────────────────────────
        if (firstWord == "pragma")
        {
            continue;
        }

        // ── for loop ──────────────────────────────────────────────────────────
        else if (firstWord == "for")
        {
            string temp_condition = "";
            // Skip init clause (up to first ';')
            while (codePerLine[pos] != ';') pos++;
            pos++;

            // Parse condition clause (up to second ';')
            while (codePerLine[pos] != ';')
            {
                if (codePerLine[pos] == '|' && codePerLine[pos+1] == '|')
                {
                    conditions.push_back(trim(temp_condition));
                    temp_condition = "";
                    pos += 2;
                }
                else if (codePerLine[pos] == '&' && codePerLine[pos+1] == '&')
                {
                    conditions.push_back(trim(temp_condition));
                    temp_condition = "";
                    pos += 2;
                }
                else
                {
                    temp_condition = temp_condition + codePerLine[pos];
                    pos++;
                }
            }
            conditions.push_back(trim(temp_condition));
            condition_count += conditions.size();

            // Advance to opening '{'  (may span multiple lines)
            while (codePerLine[pos] != '{')
            {
                if (codePerLine[pos] == '\0')
                {
                    fout << codePerLine << endl;
                    getline(fin, codePerLine);
                    pos = 0;
                }
                else pos++;
            }
            // Write the for-line, then inject assertions inside the body
            fout << codePerLine << endl;
            for (int i = 0; i < (int)conditions.size(); i++)
            {
                fout << assertSynthesizer1(conditions[i]) << endl;
                fout << assertSynthesizer2(conditions[i]) << endl;
            }
            conditions.clear();
        }

        // ── assert → rewrite as require ───────────────────────────────────────
        else if (firstWord == "assert")
        {
            string temp = "\trequire";
            while (codePerLine[pos] != '\0')
            {
                temp = temp + codePerLine[pos];
                pos++;
            }
            fout << temp << endl;
        }

        // ── if statement ──────────────────────────────────────────────────────
        else if (firstWord == "if")
        {
            string temp_condition = "";
            int openbracket = 0;
            vector<string> tempCodePerLine;

            // Skip spaces then the opening '('
            while (codePerLine[pos] == ' ') pos++;
            pos++; // skip '('

            // Parse the condition, respecting nested parens
            while (codePerLine[pos] != ')' || openbracket != 0)
            {
                if (codePerLine[pos] == '\0')
                {
                    tempCodePerLine.push_back(codePerLine);
                    pos = 0;
                    getline(fin, codePerLine);
                }
                else if (codePerLine[pos] == '|' && codePerLine[pos+1] == '|')
                {
                    conditions.push_back(trim(temp_condition));
                    temp_condition = "";
                    pos += 2;
                }
                else if (codePerLine[pos] == '&' && codePerLine[pos+1] == '&')
                {
                    conditions.push_back(trim(temp_condition));
                    temp_condition = "";
                    pos += 2;
                }
                else
                {
                    temp_condition = temp_condition + codePerLine[pos];
                    if (codePerLine[pos] == '(') openbracket++;
                    else if (codePerLine[pos] == ')') openbracket--;
                    pos++;
                }
            }
            conditions.push_back(trim(temp_condition));
            condition_count += conditions.size();

            // Determine whether the if-body uses braces or is a single statement.
            // Skip whitespace after the closing ')'.
            int lookPos = pos + 1; // pos is currently on the closing ')'
            while (lookPos < (int)codePerLine.size() &&
                   (codePerLine[lookPos] == ' ' || codePerLine[lookPos] == '\t'))
                lookPos++;

            bool hasBrace = (lookPos < (int)codePerLine.size() && codePerLine[lookPos] == '{');

            if (!hasBrace)
            {
                // Brace-less single-statement if:  emit assertions, then wrap
                // the original line in braces so the injector stays consistent.
                for (int i = 0; i < (int)conditions.size(); i++)
                {
                    fout << assertSynthesizer1(conditions[i]) << endl;
                    fout << assertSynthesizer2(conditions[i]) << endl;
                }
                // Collect continuation lines already stored
                for (int k = 0; k < (int)tempCodePerLine.size(); k++)
                    fout << tempCodePerLine[k] << endl;
                // Emit the if-line unchanged (the single-statement body follows on the same line
                // or the next line — either way it compiles correctly without braces).
                fout << codePerLine << endl;
            }
            else
            {
                // Emit assertions BEFORE the if-statement (original behaviour)
                for (int i = 0; i < (int)conditions.size(); i++)
                {
                    fout << assertSynthesizer1(conditions[i]) << endl;
                    fout << assertSynthesizer2(conditions[i]) << endl;
                }
                for (int k = 0; k < (int)tempCodePerLine.size(); k++)
                    fout << tempCodePerLine[k] << endl;
                fout << codePerLine << endl;
            }

            conditions.clear();
            tempCodePerLine.clear();
        }

        // ── require ───────────────────────────────────────────────────────────
        else if (firstWord == "require")
        {
            string temp_condition = "";
            int openbracket = 0;
            vector<string> tempCodePerLine;

            // Skip spaces then the opening '('
            while (codePerLine[pos] == ' ') pos++;
            pos++; // skip '('

            // Parse up to matching ')' or ',' (the optional error string separator)
            while ((codePerLine[pos] != ')' && codePerLine[pos] != ',') || openbracket != 0)
            {
                if (codePerLine[pos] == '\0')
                {
                    tempCodePerLine.push_back(codePerLine);
                    pos = 0;
                    getline(fin, codePerLine);
                }
                else if (codePerLine[pos] == '|' && codePerLine[pos+1] == '|')
                {
                    conditions.push_back(trim(temp_condition));
                    temp_condition = "";
                    pos += 2;
                }
                else if (codePerLine[pos] == '&' && codePerLine[pos+1] == '&')
                {
                    conditions.push_back(trim(temp_condition));
                    temp_condition = "";
                    pos += 2;
                }
                else
                {
                    temp_condition = temp_condition + codePerLine[pos];
                    if (codePerLine[pos] == '(') openbracket++;
                    else if (codePerLine[pos] == ')') openbracket--;
                    pos++;
                }
            }
            conditions.push_back(trim(temp_condition));
            condition_count += conditions.size();

            // Emit assertions BEFORE the require
            for (int i = 0; i < (int)conditions.size(); i++)
            {
                fout << assertSynthesizer1(conditions[i]) << endl;
                fout << assertSynthesizer2(conditions[i]) << endl;
            }

            // Emit continuation lines then the require itself
            for (int i = 0; i < (int)tempCodePerLine.size(); i++)
                fout << tempCodePerLine[i] << endl;
            fout << codePerLine << endl;

            conditions.clear();
            tempCodePerLine.clear();
        }

        // ── while loop ────────────────────────────────────────────────────────
        else if (firstWord == "while")
        {
            string temp_condition = "";
            int openbracket = 0;

            while (codePerLine[pos] != '(') pos++;
            pos++; // skip '('

            while (codePerLine[pos] != ')' || openbracket != 0)
            {
                if (codePerLine[pos] == '\0')
                {
                    fout << codePerLine << endl;
                    pos = 0;
                    getline(fin, codePerLine);
                }
                else if (codePerLine[pos] == '|' && codePerLine[pos+1] == '|')
                {
                    conditions.push_back(trim(temp_condition));
                    temp_condition = "";
                    pos += 2;
                }
                else if (codePerLine[pos] == '&' && codePerLine[pos+1] == '&')
                {
                    conditions.push_back(trim(temp_condition));
                    temp_condition = "";
                    pos += 2;
                }
                else
                {
                    temp_condition = temp_condition + codePerLine[pos];
                    if (codePerLine[pos] == '(') openbracket++;
                    else if (codePerLine[pos] == ')') openbracket--;
                    pos++;
                }
            }
            conditions.push_back(trim(temp_condition));
            condition_count += conditions.size();

            // Advance to opening '{'
            while (codePerLine[pos] != '{')
            {
                if (codePerLine[pos] == '\0')
                {
                    fout << codePerLine << endl;
                    getline(fin, codePerLine);
                    pos = 0;
                }
                else pos++;
            }
            // Write the while-line, then inject assertions inside the body
            fout << codePerLine << endl;
            for (int i = 0; i < (int)conditions.size(); i++)
            {
                fout << assertSynthesizer1(conditions[i]) << endl;
                fout << assertSynthesizer2(conditions[i]) << endl;
            }
            conditions.clear();
        }

        // ── everything else: pass through unchanged ───────────────────────────
        else
        {
            fout << codePerLine << endl;
        }
    }

    cout << condition_count * 2 << endl;
    fin.close();
    fout.close();
    remove(inputFileName.c_str());
    rename(outputFileName.c_str(), inputFileName.c_str());
    return 0;
}

// ── Assertion synthesizers ────────────────────────────────────────────────────

// Asserts that the condition CAN be true  (positive case)
string assertSynthesizer1(string condition)
{
    return "\tassert(" + condition + ");";
}

// Asserts that the condition CAN be false (negative case)
string assertSynthesizer2(string condition)
{
    return "\tassert(!(" + condition + "));";
}