"""RFQ parser: turns a short, messy (Hinglish) buyer message into six fields.

Pure standard library, rule based. Pipeline:
  1. normalise text            (lowercase, strip commas inside numbers, number words)
  2. find every number and decide what it is (gsm / micron / quantity / noise)
  3. pick the best quantity candidate
  4. category from keywords (with typo tolerance)
  5. location from a gazetteer, then cue-word / leftover-word heuristics
"""
import re
import unicodedata
from decimal import Decimal
from difflib import get_close_matches

# ----------------------------------------------------------------- vocabulary
MULT = {
    "k": 1000, "thousand": 1000, "thou": 1000, "hazaar": 1000, "hazar": 1000,
    "hajar": 1000, "hajaar": 1000, "hzr": 1000, "lakh": 10**5, "lakhs": 10**5,
    "lac": 10**5, "lacs": 10**5, "lkh": 10**5, "crore": 10**7, "crores": 10**7,
    "cr": 10**7, "million": 10**6, "hundred": 100, "sau": 100,
}
_MULT_ALT = "|".join(sorted(MULT, key=len, reverse=True))
MULT_RE = re.compile(rf"\s*({_MULT_ALT})(?![a-z])")

# number words, only replaced when directly followed by a multiplier: "do lakh", "five thousand"
WORD_NUM = {
    "dedh": "1.5", "dhai": "2.5", "dhaai": "2.5", "twenty five": "25", "pachees": "25",
    "one": "1", "ek": "1", "two": "2", "do": "2", "three": "3", "teen": "3",
    "four": "4", "char": "4", "chaar": "4", "five": "5", "paanch": "5", "panch": "5",
    "six": "6", "chhe": "6", "seven": "7", "saat": "7", "eight": "8", "aath": "8",
    "nine": "9", "nau": "9", "ten": "10", "das": "10", "fifteen": "15", "pandrah": "15",
    "twenty": "20", "bees": "20", "thirty": "30", "tees": "30", "forty": "40",
    "chalis": "40", "fifty": "50", "pachas": "50", "sixty": "60", "saath": "60",
    "seventy": "70", "eighty": "80", "ninety": "90",
}

GSM_U = {"gsm"}
MIC_U = {"mic", "mics", "micron", "microns", "micr", "um"}
PCS_U = {"pcs", "pc", "pieces", "piece", "pics", "pices", "peices", "pcss", "nos", "units",
         "unit", "numbers"}
KG_U = {"kg", "kgs", "kilo", "kilos", "kilogram", "kilograms", "kgm"}
MT_U = {"mt", "mts", "ton", "tons", "tonne", "tonnes", "tn"}
COUNT_NOUN = {"bag", "bags", "sack", "sacks", "box", "boxes", "carton", "cartons", "pouch",
              "pouches", "roll", "rolls", "bori", "thaila", "thaile", "jumbo", "fibc"}

# kg/mt number followed by these = fill weight / capacity of each item, not the order size
FILL_NEXT = {"capacity", "cap", "swl", "load", "loading", "each", "per", "ka", "ke", "ki",
             "wala", "wale", "wali", "size", "packing", "pack", "packs", "packet"} | COUNT_NOUN
# ... followed by these = probably fill weight, but use only if nothing better exists
WEAK_NEXT = {"pp", "hdpe", "woven", "bopp", "laminated", "printed", "plain"}
FILL_PREV = {"capacity", "cap", "swl", "size", "weight", "wt", "load", "holding"}
WEAK_PREV = COUNT_NOUN | {"of", "pack", "packing"}

# a number followed by one of these is a dimension, price, date, duration ... never the quantity
IGNORE_UNIT = {
    "inch", "inches", "in", "cm", "mm", "mtr", "mtrs", "meter", "meters", "metre", "ft", "feet",
    "ply", "layer", "layers", "color", "colour", "colors", "colours", "gm", "g", "gram", "grams",
    "ml", "ltr", "l", "litre", "litres", "liter", "days", "day", "din", "week", "weeks", "hafte",
    "month", "months", "mahine", "rs", "inr", "rupees", "rupee", "per", "th", "st", "nd", "rd",
    "am", "pm", "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct",
    "nov", "dec", "percent", "%", "x", "gauge",
}
IGNORE_PREV = {"width", "length", "height", "dia", "diameter", "thickness", "od", "id", "rs",
               "inr", "price", "rate", "budget", "phone", "mobile", "call", "whatsapp", "pin",
               "pincode", "gst", "ply"}

FUZZ_VOCAB = ["bopp", "fibc", "jumbo", "woven", "sack", "sacks", "bags", "bori", "corrugated",
              "boxes", "carton", "cartons", "mono", "folding", "duplex", "pouch", "pouches",
              "standup", "doypack", "sachet", "zipper", "film", "shrink", "stretch", "wrap",
              "micron", "microns", "pieces"]
ALIAS = {"bgs": "bags", "bg": "bag", "bop": "bopp", "boop": "bopp", "bpp": "bopp",
         "pauch": "pouch", "pouchs": "pouches", "cartoon": "carton", "cartoons": "cartons",
         "jumbu": "jumbo", "jambo": "jumbo", "ctn": "carton", "ctns": "cartons"}

# words that are not place names (filler, Hinglish glue, product descriptors)
STOP = set("""
need needed want wanted require required requirement requirements quote quotation quoting price
rate rates best urgent urgently asap please pls plz kindly send share give dena de do chahiye
chahie chaiye hai hain he h ka ki ke ko mein me mai se tak par pe wala wali wale aur or and
with without for from to at in on of the a an is are this that hello hi hey sir bhai bhaiya ji
madam dear regards thanks thank looking enquiry inquiry enq rfq order orders bulk sample samples
supply supplier quantity qty moq per each total approx approximately around about upto minimum
maximum min max size type grade quality new old good high low heavy duty strong printed print
plain laminated lamination transparent clear white black blue red green yellow color colour
colours colors food virgin recycled material pp hdpe ldpe pe pet matte glossy gloss matt zip
ziplock lock handle window vent ventilated uv treated liner inner outer open mouth top bottom
flat side gusset seal sealed centre center stand up roll rolls reel tape width length height
thickness dia diameter deliver delivery delivered dispatch ship shipping location city
destination pickup days day week month within immediately today tomorrow jaldi abhi kitna kya
kab kaise mujhe humein hume mere hamare apna apni aapka aap hum main mera bhejo bhej batao bata
karo kar karna call whatsapp contact number no mobile email mail only also plus same like ok
okay yes ready cost budget offer discount payment advance cash cod gst tax inclusive exclusive
extra free door ex factory works transport freight lorry truck container fcl lcl pallet packing
pack packs packet packets sheet sheets paper kraft brown natural jute cotton nylon cpp mpet
metallised metalized capacity swl load weight wt sales team company pvt ltd customer buyer
vendor party ply layer mic mics gsm micron microns pcs pc nos piece pieces units kg kgs mt ton
tons tonne tonnes kilo inch cm mm ft feet mtr stock available availability
""".split())

CATEGORY_WORDS = set(FUZZ_VOCAB) | COUNT_NOUN | set(ALIAS.values()) | {
    "bag", "box", "bulker", "bulk", "big", "cling", "master", "gift", "rigid", "fbb", "sbs",
    "spout", "super", "laminate", "monocarton"}

# ----------------------------------------------------------------- gazetteer
# Not exhaustive on purpose: unseen places are caught by the heuristics in _location().
_PLACES = """
mumbai delhi bengaluru bangalore hyderabad ahmedabad chennai kolkata surat pune jaipur lucknow
kanpur nagpur indore thane bhopal visakhapatnam vizag patna vadodara baroda ghaziabad ludhiana
agra nashik faridabad meerut rajkot varanasi srinagar aurangabad dhanbad amritsar allahabad
prayagraj howrah gwalior jabalpur coimbatore vijayawada jodhpur madurai raipur kota guwahati
chandigarh solapur bareilly moradabad mysore mysuru gurgaon gurugram aligarh jalandhar trichy
bhubaneswar salem warangal thiruvananthapuram trivandrum bhiwandi guntur amravati bikaner noida
jamshedpur bhilai cuttack firozabad kochi cochin bhavnagar dehradun durgapur asansol nanded
kolhapur ajmer gulbarga jamnagar ujjain siliguri jhansi jammu mangalore mangaluru erode belgaum
belagavi tirunelveli malegaon gaya jalgaon udaipur davanagere kozhikode calicut akola kurnool
rajahmundry bokaro bellary patiala agartala bhagalpur muzaffarnagar latur dhule rohtak korba
bhilwara berhampur muzaffarpur ahmednagar mathura kollam kadapa bilaspur shahjahanpur chandrapur
junagadh thrissur alwar kakinada nizamabad parbhani tumkur khammam panipat darbhanga aizawl
dewas karnal bathinda jalna eluru satna sonipat sagar rourkela durg imphal ratlam hapur arrah
anantapur karimnagar etawah bharatpur begusarai gandhinagar hosur vapi ankleshwar morbi daman
silvassa baddi mundra kandla halol sanand bharuch valsad navsari mehsana palanpur himmatnagar
gondal surendranagar anand nadiad vasai palghar bhiwadi manesar rewari neemrana mandideep
pithampur kalol dahej panoli jhagadia sachin pardi udhna kharagpur haldia hubli dharwad
hubballi tiruppur tirupur namakkal karur dindigul vellore pondicherry puducherry ernakulam
kannur palakkad alappuzha sambalpur balasore ranchi gorakhpur saharanpur roorkee haridwar
rishikesh haldwani rudrapur ambala yamunanagar hisar panchkula mohali zirakpur khanna phagwara
pathankot hoshiarpur amreli porbandar bhuj gandhidham ambernath dombivli kalyan panvel taloja
tarapur boisar satara sangli beed yavatmal wardha ichalkaranji kolar bidar raichur hassan
shimoga hospet secunderabad nellore ongole tirupati rewa katni itarsi khandwa mandsaur
kerala gujarat maharashtra rajasthan punjab haryana karnataka telangana tamilnadu odisha bihar
jharkhand assam bengal uttarakhand goa andhra himachal chhattisgarh tripura meghalaya manipur
nagaland sikkim
india usa uk uae dubai sharjah ajman doha qatar kuwait muscat oman bahrain riyadh jeddah dammam
iran iraq turkey egypt nigeria kenya ghana tanzania uganda ethiopia sudan angola mozambique
zambia zimbabwe malawi madagascar mauritius somalia senegal bangladesh dhaka chittagong nepal
kathmandu bhutan thimphu srilanka colombo pakistan karachi lahore afghanistan china shanghai
guangzhou shenzhen vietnam thailand bangkok malaysia singapore indonesia jakarta philippines
manila japan tokyo korea germany france italy spain portugal netherlands belgium poland russia
ukraine greece england london canada toronto mexico brazil argentina chile peru australia sydney
melbourne africa europe
""".split()
_MULTI = ["navi mumbai", "new delhi", "sri lanka", "abu dhabi", "saudi arabia", "hong kong",
          "south africa", "new zealand", "united states", "united kingdom", "united arab emirates",
          "uttar pradesh", "madhya pradesh", "tamil nadu", "west bengal", "andhra pradesh",
          "himachal pradesh", "mandi gobindgarh", "new york", "los angeles", "south korea",
          "ho chi minh", "kuala lumpur", "dar es salaam", "cape town", "greater noida",
          "pimpri chinchwad", "sri ganganagar"]
GAZ = set(_PLACES) | set(_MULTI)
ALL_CAPS_PLACES = {"usa", "uk", "uae"}
# broad regions lose to a city when both appear ("Wardha Maharashtra" -> Wardha)
REGIONS = set("""kerala gujarat maharashtra rajasthan punjab haryana karnataka telangana tamilnadu
odisha bihar jharkhand assam bengal uttarakhand goa andhra himachal chhattisgarh tripura meghalaya
manipur nagaland sikkim india africa europe""".split()) | {m for m in _MULTI if m.split()[-1] in
{"pradesh", "nadu", "bengal"}}

DELIVERY_CUE = {"to", "at", "in", "near", "for", "deliver", "delivery", "dispatch", "ship",
                "shipping", "location", "loc", "city", "destination", "address", "send"}
ORIGIN_CUE = {"from", "ex", "se"}
POST_CUE = {"mein", "me", "mai", "tak", "ko", "wale", "wali"}


# ----------------------------------------------------------------- helpers
def _canon(word: str) -> str:
    """Map a (possibly misspelt) word to a canonical keyword, else return it unchanged."""
    if word in ALIAS:
        return ALIAS[word]
    if len(word) < 4 or word in STOP or word in GAZ or word in CATEGORY_WORDS or word in MIC_U:
        return word
    hit = get_close_matches(word, FUZZ_VOCAB, n=1, cutoff=0.8)
    return hit[0] if hit else word


_WORD_NUM_RE = [
    (re.compile(rf"\b{w}\b(?=\s*(?:{_MULT_ALT})(?![a-z]))"), WORD_NUM[w])
    for w in sorted(WORD_NUM, key=len, reverse=True)
]


def _normalise(text: str) -> str:
    t = unicodedata.normalize("NFKC", text).lower()
    t = re.sub(r"\s*[µμ]\s*m?(?![a-z])", " micron ", t)      # 25µ, 25 μm
    t = re.sub(r"(?<=\d)\s*um\b", " micron", t)
    t = re.sub(r"\bm\.t\.?", "mt", t)                          # M.T.
    t = re.sub(r"\bg\.s\.m\.?", "gsm", t)                      # g.s.m
    t = re.sub(r"(?<=\d),(?=\d)", "", t)                       # 1,50,000 -> 150000
    for pattern, digits in _WORD_NUM_RE:                       # "do lakh" -> "2 lakh"
        t = pattern.sub(digits, t)
    return t


def _to_number(d: Decimal):
    return int(d) if d == d.to_integral_value() else float(d)


# ----------------------------------------------------------------- numbers
def _scan_numbers(t: str):
    """Classify every number. Returns (gsm, micron, quantity candidates).

    A candidate is (rank, position, value, unit); lower rank wins:
      0 explicit pieces ("5000 pcs", "200 bags")   1 weight with no fill-weight cue
      2 bare number with k/lakh/hazaar             3 bare number
      4 weight that looks like a fill weight ("bag 25 kg") - last resort
    """
    gsm = micron = None
    cands = []
    named_before = []  # "gsm 65", "mic 25": the unit word comes before the number

    for m in re.finditer(r"(?<![\w.])(\d+(?:\.\d+)?)", t):
        value = Decimal(m.group(1))
        before, rest = t[:m.start()], t[m.end():]

        if re.match(r"\s*[x×*]\s*\d", rest) or re.search(r"\d\s*[x×*]\s*$", before):
            continue                                            # dimensions: 12x18
        if re.search(r"(₹|@|\$)\s*$", before):
            continue                                            # prices

        mult = MULT_RE.match(rest)
        if mult:
            value *= MULT[mult.group(1)]
            rest = rest[mult.end():]
            sub = re.match(r"\s*(\d+(?:\.\d+)?)\s*(hazaar|hazar|thousand|k|sau|hundred)(?![a-z])", rest)
            if sub and MULT[sub.group(2)] < MULT[mult.group(1)]:  # "1 lakh 50 hazaar"
                value += Decimal(sub.group(1)) * MULT[sub.group(2)]
                rest = rest[sub.end():]

        word = re.match(r"\s*([a-z]+|%)", rest)
        unit = _canon(word.group(1)) if word else ""
        after = re.match(r"\s*[a-z]+\s+([a-z]+)", rest)
        after = _canon(after.group(1)) if after else ""
        before_word = re.search(r"([a-z]+)[\s:=\-.]*$", before)
        prev = _canon(before_word.group(1)) if before_word else ""
        pos = m.start()

        if not mult and unit in GSM_U:
            gsm = gsm if gsm is not None else int(round(value))
        elif not mult and unit in MIC_U:
            micron = micron if micron is not None else int(round(value))
        elif unit in IGNORE_UNIT:
            continue
        elif not mult and prev in GSM_U | MIC_U and unit not in KG_U | MT_U | PCS_U | COUNT_NOUN:
            named_before.append((pos, value, prev))
        elif prev in IGNORE_PREV:
            continue
        elif unit in KG_U | MT_U:
            u = "kg" if unit in KG_U else "mt"
            if prev in FILL_PREV or after in FILL_NEXT:
                continue                                        # "1000 kg capacity", "25 kg bags"
            weak = after in WEAK_NEXT or prev in WEAK_PREV
            cands.append((4 if weak else 1, pos, value, u))
        elif unit in PCS_U or unit in COUNT_NOUN:
            cands.append((0, pos, value, "pcs"))
        else:
            cands.append((2 if mult else 3, pos, value, "pcs"))

    for pos, value, kind in named_before:
        if kind in GSM_U and gsm is None:
            gsm = int(round(value))
        elif kind in MIC_U and micron is None:
            micron = int(round(value))
        else:                                                   # e.g. "65 gsm 5000": 5000 is the quantity
            cands.append((3, pos, value, "pcs"))
    return gsm, micron, cands


# ----------------------------------------------------------------- category
def _category(words, has_micron, has_gsm):
    s = set(words)
    txt = " ".join(words)
    bagish = s & {"bag", "bags", "sack", "sacks", "bori", "thaila", "thaile"}
    if s & {"fibc", "jumbo", "jumbos", "bulker"} or re.search(r"\b(big|bulk|super) (bag|sack)", txt):
        return "fibc"
    if s & {"pouch", "pouches", "standup", "doypack", "sachet", "spout", "zipper", "zip"}:
        return "pouch"
    if "bopp" in s:
        if bagish or not s & {"film", "roll", "rolls", "reel", "tape", "wrap"}:
            return "bopp_bag"
        return "film"                                           # "bopp film" is film
    if "woven" in s or (s & {"pp", "hdpe"} and bagish) or s & {"sack", "sacks", "bori"}:
        return "woven_sack"
    if s & {"mono", "folding", "duplex", "fbb", "sbs", "monocarton"}:
        return "mono_carton"
    if s & {"corrugated", "box", "boxes", "ply"}:
        return "corrugated_box"
    if s & {"carton", "cartons"}:
        return "mono_carton"
    if s & {"film", "shrink", "stretch", "wrap", "cling", "roll", "rolls", "reel", "laminate"}:
        return "film"
    # nothing explicit: guess from the specs given
    if bagish:
        return "pouch" if has_micron and not has_gsm else "woven_sack"
    if has_micron:
        return "film"
    if has_gsm:
        return "corrugated_box"
    return "film"


# ----------------------------------------------------------------- location
def _location(text: str):
    orig = re.findall(r"[A-Za-z]+", text)
    low = [w.lower() for w in orig]
    n = len(low)

    def known(w):
        return (w in STOP or w in CATEGORY_WORDS or w in GAZ or w in ALIAS or w in PCS_U
                or w in KG_U or w in MT_U or w in MIC_U or w in GSM_U or w in MULT
                or w in WORD_NUM or _canon(w) != w)

    def fmt(words):
        s = " ".join(words)
        return s.upper() if s in ALL_CAPS_PLACES else s.title()

    # 1. gazetteer hits (3-, 2-, 1-word spans); prefer one after a delivery cue, avoid "from X"
    hits, i = [], 0
    while i < n:
        for size in (3, 2, 1):
            span = " ".join(low[i:i + size])
            if i + size <= n and span in GAZ:
                prev = low[i - 1] if i else ""
                score = 2 if prev in DELIVERY_CUE else -2 if prev in ORIGIN_CUE else 0
                if span in REGIONS:
                    score -= 1
                hits.append((score, fmt(low[i:i + size])))
                i += size - 1
                break
        i += 1
    if hits:
        best = max(h[0] for h in hits)
        return [h for h in hits if h[0] == best][-1][1]

    # 2. unknown word after a delivery cue ("delivery to Shirpur") or before "me/mein/tak"
    for i, w in enumerate(low):
        if w in DELIVERY_CUE:
            j = i + 1
            while j < n and low[j] in {"to", "at", "in", "near"}:
                j += 1
            if j < n and len(low[j]) >= 3 and not known(low[j]):
                end = j + 1
                if end < n and orig[end][0].isupper() and not known(low[end]):
                    end += 1
                return fmt(low[j:end])
        if w in POST_CUE and i and len(low[i - 1]) >= 3 and not known(low[i - 1]):
            return fmt([low[i - 1]])

    # 3. a capitalised unknown word that is not the first word ("... pcs Shirpur")
    left = [w for k, w in enumerate(low)
            if k and len(w) >= 3 and not known(w) and orig[k][0].isupper() and not orig[k].isupper()]
    if left:
        return fmt([left[-1]])

    # 4. the last comma-separated chunk is one or two unknown words: "..., shirpur"
    chunks = [c for c in re.split(r"[,;|\n]", text) if c.strip()]
    if len(chunks) > 1 and not re.search(r"\d", chunks[-1]):
        w = re.findall(r"[A-Za-z]+", chunks[-1])
        if 1 <= len(w) <= 2 and all(len(x) >= 3 and not known(x.lower()) for x in w):
            return fmt([x.lower() for x in w])
    return None


# ----------------------------------------------------------------- public API
def parse_rfq(text: str) -> dict:
    t = _normalise(text)
    gsm, micron, cands = _scan_numbers(t)

    quantity, unit = 0, "pcs"
    if cands:
        _, _, value, unit = min(cands, key=lambda c: (c[0], c[1]))
        quantity = _to_number(value)

    words = [_canon(w) for w in re.findall(r"[a-z]+", t)]
    return {
        "category": _category(words, micron is not None, gsm is not None),
        "quantity": quantity,
        "unit": unit,
        "gsm": gsm,
        "micron": micron,
        "location": _location(text),
    }
