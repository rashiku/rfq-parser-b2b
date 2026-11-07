# NOTE X

## Approach
Rule-based, standard library only (no installs, runs in well under a second). Steps:
1. Normalise: lowercase, strip commas inside numbers (1,50,000), turn "do lakh / five thousand" into digits.
2. Look at **every number** and decide what it is from the word after it (and sometimes before it):
   `gsm`, `micron`, a dimension/price/date/ply (ignored), a fill weight or capacity (ignored), or a quantity candidate.
3. Pick the quantity by priority: explicit pieces ("500 pcs", "200 bags") > a weight with no fill-weight cue > bare number.
   `k` only counts as thousand when not followed by a letter, so "500 kg" is never 500,000. Decimal arithmetic avoids 110000.00000000001.
4. Category from keywords with typo tolerance (difflib) and precedence rules (fibc > pouch > bopp > woven > mono > corrugated > film).
5. Location: gazetteer (cities, states, countries) first; unseen places are caught by cue words ("delivery to X", "X mein"),
   a capitalised unknown word, or a lone unknown word after the last comma.

## Score
`python score.py my_tests.jsonl` -> ALL FIELDS 75/75 (100%), and 75/75 on each of the six fields; 0.03s.
Caveat: I wrote the tests while fixing the parser, so this is a ceiling, not an estimate of the hidden set.

## 3 messages it gets wrong (checked by running them)
1. `corrugated box 1000 pcs Pune, Mumbai` -> Mumbai. Two cities, no cue; it takes the last. Pickup vs delivery is not knowable from the text.
2. `bopp laminated woven bags 10 tonnes Bhilai` -> `bopp_bag`; a label could say `woven_sack`. The category list is ambiguous here and my precedence is a guess.
3. `1000 mono cartons 350 gsm Sivakasi tamil nadu` -> location `Tamil Nadu`, not `Sivakasi`. Sivakasi is not in the gazetteer and the unknown-word heuristics only fire after a cue, capital letter or comma.
Also: a message with no recognisable product word returns `film` and quantity `0` (the spec has no "unknown" value).

## With 1,000 real labelled examples
Keep the number rules (they are exact), but measure per field to see which rules fail. 
Use the examples to build a real city list from actual buyers, tune category keyword precedence, and, if rules plateau, train a small token tagger only for category and location. 
Split train/test by buyer.

## Knowing it works in production
Log every parse; track the share of messages with quantity 0 or no category keyword; 
sample ~50 a week for human review per field;
alert if location-null or category-fallback rate jumps (a sign of new slang or a new city list gap); 
compare against what the buyer corrects in the matching step.

## Tools
Python standard library only. 
AI Coding Assistants (Gemini and Claude) were used for generating the python codes and the synthetic Hinglish test variations in `my_tests.jsonl`.
