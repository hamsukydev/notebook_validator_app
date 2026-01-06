import streamlit as st
import json
import re
from io import StringIO
import tempfile
import os

# Regex patterns
TAG = re.compile(r"^\*\*\[([^\]]+)\]\*\*$")
META_LINE = re.compile(r'^\s*[-*]?\s*(.*?)\s*[:\-](.*)$')

def preview(lines, n=60):
    """Returns a short snippet of the first non-empty line"""
    for l in lines:
        if l.strip():
            clean = l.strip().replace('*', '').replace('`', '')
            return (clean[:n] + "...") if len(clean) > n else clean
    return "<empty cell>"

def format_error(cell_num, tag, error_type, specific_msg, content_snippet):
    """Helper to create a consistent, easy-to-read error block"""
    tag_display = f" {tag} " if tag else " <No Tag> "
    return {
        "cell": cell_num,
        "tag": tag_display,
        "type": error_type,
        "message": specific_msg,
        "snippet": content_snippet
    }

def word_count(s):
    return len(re.findall(r"\b\w+\b", s))

def parse_range(text):
    if not text: return None
    text_clean = text.lower().replace(",", "")

    # Handle "Above", "Over", "Greater than"
    if any(x in text_clean for x in ["above", "over", ">", "min"]):
        nums = [int(x) for x in re.findall(r"\d+", text_clean)]
        if nums:
            return nums[0], 999999

    # Handle "Up to", "Below", "Under"
    if any(x in text_clean for x in ["up to", "below", "under", "<", "max"]):
        nums = [int(x) for x in re.findall(r"\d+", text_clean)]
        if nums:
            return 0, nums[0]

    # Handle explicit ranges "X-Y", "X to Y"
    match = re.search(r'(\d+)\s*(?:-|–|to)\s*(\d+)', text_clean)
    if match:
        return int(match.group(1)), int(match.group(2))

    # Fallback: single number means exact match
    nums = [int(x) for x in re.findall(r"\d+", text_clean)]
    if not nums: return None
    if len(nums) == 1: return nums[0], nums[0]
    return nums[0], nums[1]

def get_metadata(nb):
    cells = nb.get("cells", [])
    if not cells: return {}
    src = "".join(cells[0].get("source", []))
    meta = {}
    for raw in src.splitlines():
        m = META_LINE.match(raw)
        if not m: continue
        key = m.group(1).replace('*', '').strip().rstrip(":").lower()
        val = m.group(2).strip().lstrip('*-').strip()
        if key:
            meta[key] = val
    return meta

def get_tags(nb):
    meta = get_metadata(nb)
    tags, errors, previews, bodies, indices = [], [], [], [], []

    cells = nb.get("cells", [])

    for i, c in enumerate(cells):
        if i == 0: continue  # Skip metadata cell

        src = "".join(c.get("source", []))
        lines = [l.rstrip("\n\r") for l in src.splitlines()]

        current_preview = preview(lines) if lines else "<empty>"

        if not lines:
            continue

        first = lines[0].strip()
        m = TAG.match(first)

        if not m:
            if any(l.strip() for l in lines):
                errors.append(format_error(
                    i, None, "Format Error",
                    "Found a cell without a **[tag]** header.",
                    current_preview
                ))
            continue

        # Check for blank line after tag
        if len(lines) < 3 or lines[1].strip() != "" or not lines[2].strip():
            errors.append(format_error(
                i, first, "Format Error",
                "The tag must be followed by exactly one blank line before content.",
                current_preview
            ))
            continue

        tag_label = "[" + m.group(1) + "]"
        tags.append(tag_label)

        body_lines = lines[2:]
        bodies.append("\n".join(body_lines))
        previews.append(preview(body_lines) if body_lines else "<empty content>")
        indices.append(i)

    return tags, errors, previews, bodies, indices, meta

def validate_structure(tags, previews, indices):
    errs = []

    def get_ctx(idx_in_list):
        if 0 <= idx_in_list < len(tags):
            return indices[idx_in_list], tags[idx_in_list], previews[idx_in_list]
        return "?", "End of File", "N/A"

    if not tags:
        errs.append({"type": "Structure Error", "message": "No tagged cells found in this notebook.", "cell": "N/A", "tag": "N/A", "snippet": "N/A"})
        return errs

    # Find the last [assistant] tag
    try:
        last_a_rev_index = tags[::-1].index("[assistant]")
        last_a = len(tags) - 1 - last_a_rev_index
    except ValueError:
        errs.append({"type": "Structure Error", "message": "Could not find any [assistant] tag. The conversation must contain at least one assistant response.", "cell": "N/A", "tag": "N/A", "snippet": "N/A"})
        return errs

    if last_a == 0:
        c, t, p = get_ctx(0)
        errs.append(format_error(c, t, "Structure Error", "The final [assistant] tag cannot be the very first cell.", p))

    # Check [turn_metadata] location
    if last_a - 2 < 0 or tags[last_a - 2] != "[turn_metadata]":
        c, t, p = get_ctx(last_a - 2)
        errs.append(format_error(
            c, t, "Structure Error",
            "The tag [turn_metadata] is missing or misplaced. It must appear two positions before the final [assistant] (before [thinking]).",
            p
        ))

    # Check [thinking] appears before final [assistant]
    if last_a - 1 < 0 or tags[last_a - 1] != "[thinking]":
        c, t, p = get_ctx(last_a - 1)
        errs.append(format_error(
            c, t, "Structure Error",
            "The tag [thinking] is missing or misplaced. It must appear immediately before the final [assistant] cell.",
            p
        ))

    # Check everything before the split point
    conv_end = last_a - 2
    
    for j in range(conv_end):
        t = tags[j]
        if t not in ("[system]", "[user]", "[thinking]", "[assistant]"):
            c, _, p = get_ctx(j)
            errs.append(format_error(
                c, t, "Structure Error",
                "Invalid tag found in the main conversation area. Only [system], [user], [thinking], or [assistant] are allowed here.",
                p
            ))
    
    # Validate conversation turn structure
    i = 0
    while i < conv_end:
        if tags[i] == "[user]":
            if i + 1 == conv_end:
                pass
            else:
                if i + 1 >= len(tags) or tags[i + 1] != "[thinking]":
                    c, t, p = get_ctx(i + 1) if i + 1 < len(tags) else get_ctx(i)
                    errs.append(format_error(
                        c, t if i + 1 < len(tags) else tags[i], "Structure Error",
                        f"Expected [thinking] after [user], but found {tags[i + 1] if i + 1 < len(tags) else 'end of file'}.",
                        p
                    ))
                elif i + 2 >= len(tags) or tags[i + 2] != "[assistant]":
                    c, t, p = get_ctx(i + 2) if i + 2 < len(tags) else get_ctx(i + 1)
                    errs.append(format_error(
                        c, t if i + 2 < len(tags) else tags[i + 1], "Structure Error",
                        f"Expected [assistant] after [thinking], but found {tags[i + 2] if i + 2 < len(tags) else 'end of file'}.",
                        p
                    ))
        i += 1

    # SINGLE FAMILY ENFORCEMENT
    model_tags = tags[last_a + 1:]
    
    if not model_tags:
        c, t, p = get_ctx(last_a)
        errs.append(format_error(
            c, t, "Structure Error",
            "No model comparison blocks found after the final assistant response.",
            p
        ))
        return errs

    families_found = set()
    for t in model_tags:
        m = re.search(r"assistant_(nemo|qwen)_", t)
        if m:
            families_found.add(m.group(1))
    
    if len(families_found) == 0:
         errs.append({"type": "Structure Error", "message": "No valid 'nemo' or 'qwen' tags found in the model block section.", "cell": "N/A", "tag": "N/A", "snippet": "N/A"})
         return errs
    
    if len(families_found) > 1:
        fam_list = ", ".join(families_found)
        c, t, p = get_ctx(last_a + 1)
        errs.append(format_error(
            c, t, "Structure Error",
            f"Mixed model families detected ({fam_list}). Please use ONLY 'nemo' blocks OR ONLY 'qwen' blocks in a single notebook.",
            p
        ))
        return errs
    
    target_family = list(families_found)[0]
    expected_index = 1

    i = last_a + 1
    n = len(tags)

    while i < n:
        if i + 3 >= n:
            c, t, p = get_ctx(i)
            errs.append(format_error(
                c, t, "Structure Error",
                "Incomplete model block definition. A block must contain 4 cells: [thinking] -> [assistant_X_#] -> [validation] -> [human_report].",
                p
            ))
            break

        t_think, t_resp, t_val, t_human = tags[i], tags[i+1], tags[i+2], tags[i+3]
        c_think, _, p_think = get_ctx(i)

        if t_think != "[thinking]":
            errs.append(format_error(
                c_think, t_think, "Structure Error",
                f"Expected [thinking] at the start of model block #{expected_index}, but found {t_think}.",
                p_think
            ))
            i += 1
            continue

        m = re.fullmatch(fr"\[assistant_{target_family}_(\d+)\]", t_resp)
        v = re.fullmatch(fr"\[assistant_{target_family}_(\d+)_validation_report\]", t_val)
        h = re.fullmatch(fr"\[assistant_{target_family}_(\d+)_human_report\]", t_human)

        if not (m and v and h):
            c_resp, _, p_resp = get_ctx(i+1)
            errs.append(format_error(
                c_resp, t_resp, "Structure Error",
                f"Invalid tag sequence. Expected [thinking] -> [assistant_{target_family}_#] -> validation -> human_report.\n"
                f"       Found: {t_think}, {t_resp}, {t_val}, {t_human}.\n"
                f"       (Note: Ensure you are not mixing 'nemo' and 'qwen' families).",
                p_resp
            ))
        else:
            idxs = {int(m.group(1)), int(v.group(1)), int(h.group(1))}

            if len(idxs) != 1:
                c_resp, _, p_resp = get_ctx(i+1)
                errs.append(format_error(
                    c_resp, t_resp, "Structure Error",
                    f"ID Mismatch. Tags have different numbers: {t_resp}, {t_val}, {t_human}. They must share the same index.",
                    p_resp
                ))
            else:
                idx = idxs.pop()
                if idx != expected_index:
                    c_resp, _, p_resp = get_ctx(i+1)
                    errs.append(format_error(
                        c_resp, t_resp, "Sequence Error",
                        f"Unexpected index for {target_family}. Expected block #{expected_index}, but found block #{idx}.",
                        p_resp
                    ))
                expected_index += 1
        i += 4

    return errs

def validate_lengths(tags, bodies, meta):
    errs = []
    if not meta:
        return errs

    def rng(key):
        v = meta.get(key)
        if not v: return None, None, None
        r = parse_range(v)
        if not r:
            errs.append({"type": "Metadata Error", "message": f"Could not parse range for '{key}'. Value found: '{v}'", "cell": "Metadata", "tag": "N/A", "snippet": "N/A"})
            return None, None, v
        return r[0], r[1], v

    min_c, max_c, c_text = rng("conversation length")
    min_s, max_s, s_text = rng("system prompt length")
    min_u, max_u, u_text = rng("user prompt length")

    try:
        last_a_idx = max(i for i, t in enumerate(tags) if t == "[assistant]")
    except ValueError:
        last_a_idx = -1

    conv_end = last_a_idx
    if last_a_idx > 1 and tags[last_a_idx - 1] == "[thinking]" and tags[last_a_idx - 2] == "[turn_metadata]":
        conv_end = last_a_idx - 2

    if conv_end >= 0:
        if min_c is not None:
            conv_tags = tags[:conv_end]
            turns = sum(1 for t in conv_tags if t == "[user]")
            if not (min_c <= turns <= max_c):
                errs.append({"type": "Length Error", "message": f"Conversation turns ({turns}) outside range ({c_text}).", "cell": "N/A", "tag": "N/A", "snippet": "N/A"})

        if min_s is not None:
            sys_words = sum(word_count(bodies[i]) for i, t in enumerate(tags[:conv_end]) if t == "[system]")
            if sys_words > 0:
                if not (min_s <= sys_words <= max_s):
                     errs.append({"type": "Length Error", "message": f"System prompt words ({sys_words}) outside range ({s_text}).", "cell": "N/A", "tag": "N/A", "snippet": "N/A"})

        if min_u is not None:
            user_counts = [word_count(bodies[i]) for i, t in enumerate(tags[:conv_end]) if t == "[user]"]
            if user_counts:
                avg = int(round(sum(user_counts) / len(user_counts)))
                if not (min_u <= avg <= max_u):
                    errs.append({"type": "Length Error", "message": f"Avg user prompt words ({avg}) outside range ({u_text}).", "cell": "N/A", "tag": "N/A", "snippet": "N/A"})

    return errs

def extract_json_from_body(body):
    lines = body.splitlines()
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i == len(lines):
        return None, "Cell is empty. Expected a ```json block."

    first = lines[i].strip()
    if not re.fullmatch(r"```+\s*json\s*", first, re.I):
        return None, "Content must start explicitly with ```json"

    j = i + 1
    while j < len(lines) and not lines[j].strip().startswith("```"):
        j += 1

    if j == len(lines):
        return None, "Missing closing ``` for the JSON block."

    return "\n".join(lines[i+1:j]), None

def validate_json_cells(tags, bodies, previews, indices):
    errs = []
    for i, t in enumerate(tags):
        if t == "[turn_metadata]" or \
           re.fullmatch(r"\[assistant_(nemo|qwen)_\d+_validation_report\]", t) or \
           re.fullmatch(r"\[assistant_(nemo|qwen)_\d+_human_report\]", t):

            js, err = extract_json_from_body(bodies[i])
            if err:
                errs.append(format_error(
                    indices[i], t, "JSON Format Error",
                    err, previews[i]
                ))
                continue
            try:
                json.loads(js)
            except Exception as e:
                errs.append(format_error(
                    indices[i], t, "JSON Syntax Error",
                    str(e), previews[i]
                ))
    return errs

def validate_notebook(nb):
    """Main validation function that returns results"""
    tags, cell_errors, previews, bodies, indices, meta = get_tags(nb)
    
    structure_errors = validate_structure(tags, previews, indices)
    length_errors = validate_lengths(tags, bodies, meta)
    json_errors = validate_json_cells(tags, bodies, previews, indices)
    
    all_errors = cell_errors + structure_errors + length_errors + json_errors
    
    return {
        "valid": len(all_errors) == 0,
        "errors": all_errors,
        "metadata": meta,
        "stats": {
            "total_cells": len(tags),
            "format_errors": len(cell_errors),
            "structure_errors": len(structure_errors),
            "length_errors": len(length_errors),
            "json_errors": len(json_errors)
        }
    }

# Streamlit UI
def main():
    st.set_page_config(
        page_title="Notebook Validator",
        page_icon="📓",
        layout="wide"
    )
    
    st.title("📓 Jupyter Notebook Validator")
    st.markdown("Upload your `.ipynb` notebooks to validate their structure, formatting, and content.")
    
    # Sidebar for information
    with st.sidebar:
        st.header("About")
        st.markdown("""
        This validator checks:
        - **Structure**: Proper tag sequencing
        - **Format**: Tag formatting and spacing
        - **Length**: Conversation and prompt lengths
        - **JSON**: Syntax in metadata/reports
        - **Model Families**: Single family consistency
        """)
        
        st.header("Valid Tags")
        st.code("""
[system]
[user]
[thinking]
[assistant]
[turn_metadata]
[assistant_nemo_#]
[assistant_qwen_#]
[assistant_X_#_validation_report]
[assistant_X_#_human_report]
        """)
    
    # File uploader
    uploaded_files = st.file_uploader(
        "Choose notebook file(s)",
        type=['ipynb'],
        accept_multiple_files=True,
        help="Upload one or more .ipynb files for validation"
    )
    
    if uploaded_files:
        st.markdown("---")
        
        # Summary metrics
        total_files = len(uploaded_files)
        valid_count = 0
        invalid_count = 0
        
        results = []
        
        # Process each file
        for uploaded_file in uploaded_files:
            try:
                # Load notebook
                nb_content = uploaded_file.read()
                nb = json.loads(nb_content)
                
                # Validate
                result = validate_notebook(nb)
                result['filename'] = uploaded_file.name
                results.append(result)
                
                if result['valid']:
                    valid_count += 1
                else:
                    invalid_count += 1
                    
            except Exception as e:
                st.error(f"❌ Failed to process {uploaded_file.name}: {str(e)}")
        
        # Display summary
        col1, col2, col3 = st.columns(3)
        with col1:
            st.metric("Total Files", total_files)
        with col2:
            st.metric("✅ Valid", valid_count)
        with col3:
            st.metric("❌ Invalid", invalid_count)
        
        st.markdown("---")
        
        # Display results for each file
        for result in results:
            filename = result['filename']
            
            if result['valid']:
                st.success(f"✅ **{filename}** - VALID")
                
                # Show metadata if available
                if result['metadata']:
                    with st.expander("📋 Metadata", expanded=False):
                        for key, value in result['metadata'].items():
                            st.text(f"{key}: {value}")
                
            else:
                st.error(f"❌ **{filename}** - INVALID")
                
                # Show statistics
                stats = result['stats']
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("Format Errors", stats['format_errors'])
                with col2:
                    st.metric("Structure Errors", stats['structure_errors'])
                with col3:
                    st.metric("Length Errors", stats['length_errors'])
                with col4:
                    st.metric("JSON Errors", stats['json_errors'])
                
                # Show errors
                with st.expander("🔍 View Errors", expanded=True):
                    for idx, error in enumerate(result['errors'], 1):
                        error_type = error.get('type', 'Error')
                        cell = error.get('cell', 'N/A')
                        tag = error.get('tag', 'N/A')
                        message = error.get('message', '')
                        snippet = error.get('snippet', '')
                        
                        # Color code by error type
                        if 'Format' in error_type:
                            color = "🔴"
                        elif 'Structure' in error_type:
                            color = "🟠"
                        elif 'Length' in error_type:
                            color = "🟡"
                        elif 'JSON' in error_type:
                            color = "🔵"
                        else:
                            color = "⚪"
                        
                        st.markdown(f"""
                        **{color} Error #{idx}** - Cell {cell} {tag}
                        - **Type:** {error_type}
                        - **Message:** {message}
                        - **Snippet:** `{snippet}`
                        """)
                        st.markdown("---")
                
                # Show metadata if available
                if result['metadata']:
                    with st.expander("📋 Metadata", expanded=False):
                        for key, value in result['metadata'].items():
                            st.text(f"{key}: {value}")
            
            st.markdown("---")
    
    else:
        st.info("👆 Upload one or more notebook files to begin validation")
        
        # Show example
        with st.expander("📖 Example notebook structure"):
            st.markdown("""
            ```
            Cell 0: Metadata
            - Conversation length: X-Y
            - System prompt length: X-Y
            - User prompt length: X-Y
            
            Cell 1: **[system]**
            
            System prompt content...
            
            Cell 2: **[user]**
            
            User message...
            
            Cell 3: **[thinking]**
            
            Internal reasoning...
            
            Cell 4: **[assistant]**
            
            Assistant response...
            ```
            """)

if __name__ == "__main__":
    main()
