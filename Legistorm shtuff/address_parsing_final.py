import re
import pandas as pd
from pathlib import Path
# Config
INPUT_FOLDER = Path(r"C:\Users\watri\Dropbox\Legistorm Office Location\Congress Biography Text Files\105th_Congress_Text")
OUTPUT_FOLDER  = Path("105th_congress_office_locations.csv")

# used to extract relevant info from the title
FILENAME_RE = re.compile(
    r"CDIR-\d{4}-\d{2}-\d{2}-"   # date prefix
    r"(?P<state>[A-Z]{2})-"     # state code
    r"(?P<chamber>[HS])-"       # H or S
    r"(?P<district>\d+)\.txt$"  # district number
)
ZIP_RE = re.compile(r"\b\d{5}(?:-\d{4})?\b")
PO_BOX_RE = re.compile(r"\b(?:P\.?O\.?|PO|O\.?)\s*Box\b", flags=re.IGNORECASE)

def extract_filename_info(filepath: str) -> list:
    """
    return [state_abbr, chamber, district_number].
    """
    name = Path(filepath).name
    m = FILENAME_RE.match(name)
    return [
        m.group("state"),
        m.group("chamber"),
        int(m.group("district"))
    ]
def extract_office_block(text: str, filename: str = None) -> str:
    """
    Return the raw text between 'Office Listings' and the first occurrence
    of 'Counties'.  If the marker is missing, log a warning (with the
    filename, if provided) and return an empty string.
    """
    parts = text.split("Office Listings", 1)
    if len(parts) < 2:
        if filename:
            print(f"[WARNING] 'Office Listings' section missing in file {filename}")
        else:
            print("[WARNING] 'Office Listings' section missing (no filename provided)")
        return ""
    block = parts[1]
    cut = re.split(r"\bCounties\b", block, 1)[0]
    return cut



def extract_name_and_party(text: str) -> list[str]:
    """
    After the GPO marker, split on commas up through the
    first four commas so we can inspect groups 1, 2, or 3
    for a party keyword. If none match, return "PARTY NOT FOUND".
    """
    # 1) Isolate text after the GPO bracket
    marker = "[From the U.S. Government Publishing Office"
    try:
        after = text.split(marker, 1)[1]
    except IndexError:
        raise ValueError("Missing GPO marker")
    if "]" in after:
        after = after.split("]", 1)[1]
    after = after.lstrip()

    # 2) Split into at most 5 pieces (name + four subsequent groups) (handles cases with suffixes like First, Last, M.D, Party)
    parts = [p.strip() for p in after.split(",", 4)]
    name_chunk   = parts[0]
    first_after  = parts[1] if len(parts) > 1 else ""
    second_after = parts[2] if len(parts) > 2 else ""
    third_after  = parts[3] if len(parts) > 3 else ""

    # 3) Look for party in first, then second, then third group
    parties = {"Republican", "Democrat", "Independent"}

    if first_after in parties:
        name, party = name_chunk, first_after
    elif second_after in parties:
        name, party = f"{name_chunk} {first_after}", second_after
    elif third_after in parties:
        suffix = " ".join(filter(None, [first_after, second_after]))
        name, party = f"{name_chunk} {suffix}", third_after
    else:
        name, party = name_chunk, "PARTY NOT FOUND"

    # 5) Title‐case name (keeps punctuation in suffix)
    return [name.title(), party]



def is_suspicious_address(addr: str) -> bool:
    """
    Flag suspicious addresses that may be that need manual review.
    """
    addr = addr.strip()

    # Check length
    if len(addr) < 20 or len(addr) > 200:
        return True

    # Check for ZIP presence
    if not ZIP_RE.search(addr):
        return True

    # Check if it's mostly punctuation or just a few words
    if len(addr.split()) < 3:
        return True

    # Check if it lacks digits (e.g., missing street number)
    if not any(char.isdigit() for char in addr):
        return True

    # Suspicious characters or patterns
    if ",," in addr or "  " in addr:
        return True

    # Check if its a P.O box address
    if PO_BOX_RE.search(addr):
        return True
        return True

    return False


def parse_office_listings_new(text: str) -> list[str]:
    """
    Parse office addresses by splitting on periods.
    Removes phone/FAX, skips URLs, bracketed & staff lines (--),
    then for each segment that ends in STATE ZIP, returns it.
    """
    # Extract the section of text with addressses in it
    block = extract_office_block(text)

    # 1) Strip phone & fax
    block = re.sub(r"\(\d{3}\)\s*\d{3}-\d{4}", "", block)
    block = re.sub(r"FAX:\s*\d{3}-\d{4}", "", block, flags=re.IGNORECASE)

    # 2) Remove unwanted lines
    lines = block.splitlines()
    filtered = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith(("http", "[")) or "--" in s:
            continue
        filtered.append(s)

    merged = " ".join(filtered)

    # 3) Split on every period that is not P.O to get candidate segments
    parts = re.split(r"(?<!P\.O)\.\s*", merged)

    # 4) Keep only those ending with STATE ZIP
    ZIP_END_RE = re.compile(r"\b[A-Z]{2}\s+\d{5}(?:-\d{4})?\b$")
    addresses = []
    for seg in parts:
        seg = seg.strip()
        # skip empty or pure ZIPs
        if not seg or re.fullmatch(r"\d{5}(?:-\d{4})?", seg):
            continue
        if ZIP_END_RE.search(seg):
            addresses.append(seg)

    return addresses




# ---------Edge case stuff------
def merge_split_segments(addresses: list[str]) -> list[str]:
    """
    Only merge segments that start with a comma (',') into the previous address.
    All other segments (including building names or PO Boxes) stand alone.
    """
    merged = []
    for seg in addresses:
        seg = seg.strip()
        if seg.startswith(","):
            # continuation of the previous address
            if merged:
                # strip leading comma then append
                merged[-1] = f"{merged[-1]} {seg.lstrip(',').strip()}"
            else:
                # no previous—just add without the comma
                merged.append(seg.lstrip(',').strip())
        else:
            # new address or valid building name / PO Box
            merged.append(seg)
    return merged


def split_multiple_addresses(addresses: list[str]) -> list[str]:
    """
    For any address string containing multiple ZIPs, split into segments:
      - Address 1: from start up through the first ZIP that follows a state code
      - Address 2: from just after that ZIP up through the next, and so on.
    Only ZIPs preceded by an uppercase state code and a space (e.g. "AK ") are treated as split points.
    """
    out: list[str] = []
    for addr in addresses:
        # Find only those ZIP matches that are preceded by a state code (e.g. "AK ")
        matches = [
            m for m in ZIP_RE.finditer(addr)
            if re.fullmatch(r"[A-Z]{2}\s+", addr[max(0, m.start() - 3) : m.start()])
        ]

        # If there's at most one real ZIP, keep the whole thing
        if len(matches) <= 1:
            out.append(addr.strip())
            continue

        # Otherwise, split between each state‑ZIP boundary
        for i, m in enumerate(matches):
            start = matches[i - 1].end() if i > 0 else 0
            end = m.end()
            segment = addr[start:end].strip(" ,.")
            out.append(segment)

    return out

#------



def main():
    rows = []

    for txt_path in INPUT_FOLDER.glob("CDIR-*.txt"):
        text = txt_path.read_text(encoding="utf-8", errors="ignore")

        # filename metadata
        state, chamber, district = extract_filename_info(str(txt_path))

        # name & party
        name, party = extract_name_and_party(text)

        # try to get the office block
        block = extract_office_block(text, txt_path.name)
        if not block:
            # append a “missing” row
            rows.append({
                "name":        txt_path.name,
                "party":       "",
                "address":     "",
                "state":       state,
                "chamber":     chamber,
                "district":    district,
                "suspicious":  True,
                "error":       "Office Listings missing"
            })
            continue

        # otherwise proceed as before
        addresses = parse_office_listings_new(text)
        addresses = merge_split_segments(addresses)
        addresses = split_multiple_addresses(addresses)

        for addr in addresses:
            rows.append({
                "name":       name,
                "party":      party,
                "address":    addr,
                "state":      state,
                "chamber":    chamber,
                "district":   district,
                "suspicious": is_suspicious_address(addr),
                "error":      ""
            })

    # include the new “error” column when you build your DataFrame
    df = pd.DataFrame(rows, columns=[
        "name", "party", "address", "state",
        "chamber", "district", "suspicious", "error"
    ])
    df.to_csv(OUTPUT_FOLDER, index=False)
    print(f"Done! Wrote {len(df)} rows to {OUTPUT_FOLDER}")

if __name__ == "__main__":
    main()

