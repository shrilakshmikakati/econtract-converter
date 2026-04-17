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
    if (argc < 2) {
        cerr << "Usage: " << args[0] << " <input_file.sol>" << endl;
        return 1;
    }

    ifstream fin;
    ofstream fout;

    string inputFileName  = args[1];
    string outputFileName = inputFileName + ".temp";
    int condition_count   = 0;

    fin.open(inputFileName);
    if (!fin.is_open()) {
        cerr << "Error: cannot open input file: " << inputFileName << endl;
        return 1;
    }
    fout.open(outputFileName);
    if (!fout.is_open()) {
        cerr << "Error: cannot open output file: " << outputFileName << endl;
        fin.close();
        return 1;
    }

    string codePerLine;
    while (getline(fin, codePerLine))
    {
        vector<string> conditions;
        string firstWord = "";
        int pos = 0;

        // Skip leading whitespace
        while (pos < (int)codePerLine.size() &&
               (codePerLine[pos] == ' ' || codePerLine[pos] == '\t'))
            pos++;

        // Skip blank lines (empty string or only whitespace)
        if (pos >= (int)codePerLine.size())
            continue;

        // Extract first word (stops at space, '(' or end-of-string)
        while (pos < (int)codePerLine.size() &&
               codePerLine[pos] != ' ' && codePerLine[pos] != '(')
        {
            firstWord = firstWord + codePerLine[pos];
            pos++;
        }

        // ── pragma: pass through the contract's own pragma unchanged ─────────
        // (The old code always replaced it with "pragma solidity >=0.4.24;"
        //  which conflicts with ^0.8.16 contracts produced by the postprocessor.)
        if (firstWord == "pragma")
        {
            fout << codePerLine << endl;
        }

        // ── for loop ──────────────────────────────────────────────────────────
        else if (firstWord == "for")
        {
            string temp_condition = "";
            // Skip init clause (up to first ';') — guard against malformed input
            while (pos < (int)codePerLine.size() && codePerLine[pos] != ';') pos++;
            if (pos < (int)codePerLine.size()) pos++; // skip the ';'

            // Parse condition clause (up to second ';')
            while (pos < (int)codePerLine.size() && codePerLine[pos] != ';')
            {
                if (pos + 1 < (int)codePerLine.size() &&
                    codePerLine[pos] == '|' && codePerLine[pos+1] == '|')
                {
                    conditions.push_back(trim(temp_condition));
                    temp_condition = "";
                    pos += 2;
                }
                else if (pos + 1 < (int)codePerLine.size() &&
                         codePerLine[pos] == '&' && codePerLine[pos+1] == '&')
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
            while (pos >= (int)codePerLine.size() || codePerLine[pos] != '{')
            {
                if (pos >= (int)codePerLine.size())
                {
                    fout << codePerLine << endl;
                    if (!getline(fin, codePerLine)) break;
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
            while (pos < (int)codePerLine.size())
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
            while (pos < (int)codePerLine.size() && codePerLine[pos] == ' ') pos++;
            if (pos < (int)codePerLine.size()) pos++; // skip '('

            // Parse the condition, respecting nested parens
            while (pos >= (int)codePerLine.size() || codePerLine[pos] != ')' || openbracket != 0)
            {
                if (pos >= (int)codePerLine.size())
                {
                    tempCodePerLine.push_back(codePerLine);
                    pos = 0;
                    if (!getline(fin, codePerLine)) break;
                }
                else if (pos + 1 < (int)codePerLine.size() &&
                         codePerLine[pos] == '|' && codePerLine[pos+1] == '|')
                {
                    conditions.push_back(trim(temp_condition));
                    temp_condition = "";
                    pos += 2;
                }
                else if (pos + 1 < (int)codePerLine.size() &&
                         codePerLine[pos] == '&' && codePerLine[pos+1] == '&')
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
            // Skip whitespace after the closing ')' — may need to look at the next line
            // if the '{' is on a line of its own (e.g. Allman style).
            int lookPos = pos + 1; // pos is currently on the closing ')'
            while (lookPos < (int)codePerLine.size() &&
                   (codePerLine[lookPos] == ' ' || codePerLine[lookPos] == '\t'))
                lookPos++;

            bool hasBrace = false;
            if (lookPos < (int)codePerLine.size())
            {
                hasBrace = (codePerLine[lookPos] == '{');
            }
            else
            {
                // '{' may be on the next line — peek ahead without consuming
                string nextLine;
                if (getline(fin, nextLine))
                {
                    int nlPos = 0;
                    while (nlPos < (int)nextLine.size() &&
                           (nextLine[nlPos] == ' ' || nextLine[nlPos] == '\t'))
                        nlPos++;
                    hasBrace = (nlPos < (int)nextLine.size() && nextLine[nlPos] == '{');
                    // Emit continuation lines already stored, then the if-line,
                    // then the peeked next line so nothing is lost.
                    for (int k = 0; k < (int)tempCodePerLine.size(); k++)
                        fout << tempCodePerLine[k] << endl;
                    for (int i = 0; i < (int)conditions.size(); i++)
                    {
                        fout << assertSynthesizer1(conditions[i]) << endl;
                        fout << assertSynthesizer2(conditions[i]) << endl;
                    }
                    fout << codePerLine << endl;
                    fout << nextLine << endl;
                    conditions.clear();
                    tempCodePerLine.clear();
                    continue; // already emitted everything — go to next iteration
                }
            }

            if (!hasBrace)
            {
                // Brace-less single-statement if: emit assertions BEFORE the if,
                // then wrap the if + its inline body in an explicit block so that
                // any variable declaration on the same or next line stays inside
                // a valid Solidity block (prevents "Variable declarations can only
                // be used inside blocks" when the body is e.g. `uint256 x = ...`).
                for (int i = 0; i < (int)conditions.size(); i++)
                {
                    fout << assertSynthesizer1(conditions[i]) << endl;
                    fout << assertSynthesizer2(conditions[i]) << endl;
                }
                // Emit any continuation lines collected while parsing multi-line conditions
                for (int k = 0; k < (int)tempCodePerLine.size(); k++)
                    fout << tempCodePerLine[k] << endl;
                // Wrap the if-statement in braces so the body is always inside a block.
                // The original single-statement body (inline or on the next line) is
                // enclosed: `{ <if-line> }` — valid Solidity for any body kind.
                fout << "{" << endl;
                fout << codePerLine << endl;
                fout << "}" << endl;
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
            while (pos < (int)codePerLine.size() && codePerLine[pos] == ' ') pos++;
            if (pos < (int)codePerLine.size()) pos++; // skip '('

            // Parse up to matching ')' or ',' (the optional error string separator)
            while (pos < (int)codePerLine.size() &&
                   ((codePerLine[pos] != ')' && codePerLine[pos] != ',') || openbracket != 0))
            {
                if (pos >= (int)codePerLine.size())
                {
                    tempCodePerLine.push_back(codePerLine);
                    pos = 0;
                    if (!getline(fin, codePerLine)) break;
                }
                else if (pos + 1 < (int)codePerLine.size() &&
                         codePerLine[pos] == '|' && codePerLine[pos+1] == '|')
                {
                    conditions.push_back(trim(temp_condition));
                    temp_condition = "";
                    pos += 2;
                }
                else if (pos + 1 < (int)codePerLine.size() &&
                         codePerLine[pos] == '&' && codePerLine[pos+1] == '&')
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

            // Advance to '(' — guard against malformed input
            while (pos < (int)codePerLine.size() && codePerLine[pos] != '(') pos++;
            if (pos < (int)codePerLine.size()) pos++; // skip '('

            while (pos >= (int)codePerLine.size() || codePerLine[pos] != ')' || openbracket != 0)
            {
                if (pos >= (int)codePerLine.size())
                {
                    // Do NOT emit the partial line here — store it and continue
                    // (the old code emitted codePerLine mid-parse, corrupting output order)
                    pos = 0;
                    if (!getline(fin, codePerLine)) break;
                }
                else if (pos + 1 < (int)codePerLine.size() &&
                         codePerLine[pos] == '|' && codePerLine[pos+1] == '|')
                {
                    conditions.push_back(trim(temp_condition));
                    temp_condition = "";
                    pos += 2;
                }
                else if (pos + 1 < (int)codePerLine.size() &&
                         codePerLine[pos] == '&' && codePerLine[pos+1] == '&')
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
            while (pos >= (int)codePerLine.size() || codePerLine[pos] != '{')
            {
                if (pos >= (int)codePerLine.size())
                {
                    fout << codePerLine << endl;
                    if (!getline(fin, codePerLine)) break;
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