import streamlit as st
import json
import re
import os
import tempfile
from collections import Counter
from typing import List, Tuple, Dict, Any
import traceback

# Configuration Constants
MIN_TURN_METADATA = 8
MIN_FAIL_PERCENTAGE = 50
MIN_HUMAN_PASS_PERCENTAGE = 100
RESTRICTED_INSTRUCTIONS = [
    "length_constraints:number_characters",
    "length_constraints:number_words",
    "length_constraints:sentence_length",
    "length_constraints:word_length",
    "length_constraints:avg_word_length",
    "length_constraints:paragraph_length",
    "change_case:lowercase_word_frequency",
    "change_case:capital_word_frequency",
    "change_case:vowel_consonant_balance",
    "keywords:letter_frequency",
    "keywords:vowel_count",
    "keywords:consonant_count",
    "keywords:alliteration",
    "keywords:palindrome_word",
    "keywords:positioning",
    "punctuation:question_exclaim",
    "detectable_format:max_paragraph_length",
    "detectable_content:numeric_inclusion"
]
CHECK_MISALIGNED_VALIDATION = True

# Utility Functions
def word_count(text: str) -> int:
    """Count words in text"""
    return len(text.split())

def format_error(index: int, tag: str, error_type: str, details: str, preview: str = "") -> str:
    """Format error message"""
    return f"🔴 Cell {index} [{tag}] - {error_type}: {details}\n   Preview: {preview[:100]}..."

def evaluate_results(results: List[Dict]) -> Dict:
    """Evaluate validation results"""
    status_counts = Counter(item["status"] for item in results)
    llm_status_counts = Counter(item["status"] for item in results if "llm_judge_" in item["id"])
    
    total = len(results)
    passed = status_counts.get("Passed", 0)
    failed = status_counts.get("Failed", 0)
    
    pass_percentage = (passed / total) * 100 if total else 0
    fail_percentage = (failed / total) * 100 if total else 0
    
    return {
        "total_length": total,
        "passed": passed,
        "failed": failed,
        "pass_percentage": round(pass_percentage, 2),
        "fail_percentage": round(fail_percentage, 2),
        "llm_passed": llm_status_counts.get("Passed", 0),
        "llm_failed": llm_status_counts.get("Failed", 0),
    }

def extract_json_from_body(body: str) -> Tuple[str, str]:
    """Extract JSON from markdown code block"""
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

def get_tags(filepath: str) -> Tuple[List, List, List, List, List, Dict]:
    """Extract tags and metadata from notebook"""
    with open(filepath, 'r', encoding='utf-8') as f:
        nb = json.load(f)
    
    tags = []
    bodies = []
    previews = []
    indices = []
    cell_errors = []
    meta = {}
    
    # Extract metadata
    if "metadata" in nb and "length_constraints" in nb["metadata"]:
        meta = nb["metadata"]["length_constraints"]
    
    cells = nb.get("cells", [])
    
    for idx, cell in enumerate(cells):
        cell_tags = cell.get("metadata", {}).get("tags", [])
        source = "".join(cell.get("source", []))
        
        if not cell_tags:
            continue
        
        tag = cell_tags[0]
        tags.append(tag)
        bodies.append(source)
        indices.append(idx)
        
        # Create preview
        first_line = source.split("\n")[0] if source else ""
        previews.append(first_line[:50])
    
    return tags, cell_errors, previews, bodies, indices, meta

def validate_structure(tags: List[str], previews: List[str], indices: List[int]) -> List[str]:
    """Validate notebook structure"""
    errs = []
    
    # Check for required tags
    if "[system]" not in tags:
        errs.append("❌ Missing required [system] tag")
    if "[turn_metadata]" not in tags:
        errs.append("❌ Missing required [turn_metadata] tag")
    
    # Find conversation end
    try:
        conv_end = tags.index("[conversation_end]")
    except ValueError:
        errs.append("❌ Missing [conversation_end] tag")
        return errs
    
    # Validate conversation structure
    conv_tags = tags[:conv_end]
    
    # Check alternating pattern
    user_assistant_tags = [t for t in conv_tags if t in ["[user]", "[assistant_nemo]", "[assistant_qwen]"]]
    
    if not user_assistant_tags:
        errs.append("❌ No conversation turns found")
        return errs
    
    for i in range(len(user_assistant_tags) - 1):
        if user_assistant_tags[i] == "[user]" and user_assistant_tags[i+1] == "[user]":
            errs.append(f"❌ Consecutive [user] tags found - pattern broken")
        elif user_assistant_tags[i] in ["[assistant_nemo]", "[assistant_qwen]"] and \
             user_assistant_tags[i+1] in ["[assistant_nemo]", "[assistant_qwen]"]:
            errs.append(f"❌ Consecutive assistant tags found - pattern broken")
    
    return errs

def validate_lengths(tags: List[str], bodies: List[str], meta: Dict) -> List[str]:
    """Validate length constraints"""
    errs = []
    
    # Parse metadata
    c_text = meta.get("conversation_turns", "")
    s_text = meta.get("system_prompt_words", "")
    u_text = meta.get("user_prompt_words", "")
    
    # Parse ranges
    def parse_range(text):
        if not text:
            return None, None
        m = re.match(r"(\d+)\s*-\s*(\d+)", text)
        if m:
            return int(m.group(1)), int(m.group(2))
        return None, None
    
    min_c, max_c = parse_range(c_text)
    min_s, max_s = parse_range(s_text)
    min_u, max_u = parse_range(u_text)
    
    # Find conversation end
    try:
        conv_end = tags.index("[conversation_end]")
    except ValueError:
        return errs
    
    # Check turn count
    if min_c is not None:
        conv_tags = tags[:conv_end]
        turns = sum(1 for t in conv_tags if t == "[user]")
        if not (min_c <= turns <= max_c):
            errs.append(f"📏 Length Error: Conversation turns ({turns}) outside range ({c_text})")
    
    # Check system prompt
    if min_s is not None:
        sys_words = sum(word_count(bodies[i]) for i, t in enumerate(tags[:conv_end]) if t == "[system]")
        if sys_words > 0:
            if not (min_s <= sys_words <= max_s):
                errs.append(f"📏 Length Error: System prompt words ({sys_words}) outside range ({s_text})")
    
    # Check user prompts
    if min_u is not None:
        for i, t in enumerate(tags[:conv_end]):
            if t == "[user]":
                user_count = word_count(bodies[i])
                if user_count:
                    if not (min_u <= user_count <= max_u):
                        errs.append(f"📏 Length Error: User prompt words ({user_count}) outside range ({u_text})")
    
    return errs

def validate_json_cells(tags: List[str], bodies: List[str], previews: List[str], indices: List[int]) -> List[str]:
    """Validate JSON formatting in cells"""
    errs = []
    
    for i, t in enumerate(tags):
        if t == "[turn_metadata]" or \
           re.fullmatch(r"\[assistant_(nemo|qwen)_\d+_validation_report\]", t) or \
           re.fullmatch(r"\[assistant_(nemo|qwen)_\d+_human_report\]", t):
            
            js, err = extract_json_from_body(bodies[i])
            if err:
                errs.append(format_error(indices[i], t, "JSON Format Error", err, previews[i]))
                continue
            
            try:
                json.loads(js)
            except Exception as e:
                errs.append(format_error(indices[i], t, "JSON Syntax Error", str(e), previews[i]))
    
    return errs

def validate_report_len_cells(tags: List[str], bodies: List[str], previews: List[str], indices: List[int]) -> List[str]:
    """Validate report cells and metadata"""
    errs = []
    basic_validation = {}
    llm_results = None
    hllm_results = None
    
    for i, t in enumerate(tags):
        if t == "[turn_metadata]" or \
           re.fullmatch(r"\[assistant_(nemo|qwen)_\d+_validation_report\]", t) or \
           re.fullmatch(r"\[assistant_(nemo|qwen)_\d+_human_report\]", t):
            
            js, err = extract_json_from_body(bodies[i])
            if err:
                errs.append(format_error(indices[i], t, "JSON Format Error", err, previews[i]))
                continue
            
            try:
                parsed_json = json.loads(js)
                
                if t == "[turn_metadata]":
                    basic_validation[t] = {
                        "instructions": len(parsed_json.get("instructions", [])),
                        "llm_judge": len(parsed_json.get("llm_judge", []))
                    }
                    basic_validation[t]["total"] = basic_validation[t]["instructions"] + basic_validation[t]["llm_judge"]
                    
                    instructionset = parsed_json.get("instructions", [])
                    if (len(instructionset) + len(parsed_json.get("llm_judge", []))) < MIN_TURN_METADATA:
                        errs.append(format_error(
                            indices[i], t, f"Minimum {MIN_TURN_METADATA} turn_metadata",
                            "length validation", previews[i]
                        ))
                    
                    if instructionset:
                        word_checks = [instr for instr in instructionset 
                                     if instr.get("instruction_id") in RESTRICTED_INSTRUCTIONS]
                        if word_checks:
                            errs.append(format_error(
                                indices[i], t, "Restricted Instructions in turn_metadata",
                                str(word_checks), previews[i]
                            ))
                
                else:
                    results = parsed_json.get("results") if isinstance(parsed_json, dict) else parsed_json
                    report = evaluate_results(results)
                    basic_validation[t] = report
                    
                    turn_metadata_report = basic_validation.get("[turn_metadata]")
                    
                    if re.fullmatch(r"\[assistant_(nemo|qwen)_\d+_validation_report\]", t):
                        if report.get("total_length") != turn_metadata_report.get("total"):
                            errs.append(format_error(
                                indices[i], t, "Turn_MetaData Validation Report coverage Error",
                                f"TMD:{turn_metadata_report.get('total')} is not {t}:{report.get('total_length')}", 
                                str(report)
                            ))
                        
                        try:
                            passed = [x for x in results if x["status"].lower() == "passed"]
                            failed = [x for x in results if x["status"].lower() == "failed"]
                            llm_results = [f"{_.get('id')}_Passed" for _ in passed if "llm_judge_" in _.get("id")] + \
                                        [f"{_.get('id')}_Failed" for _ in failed if "llm_judge_" in _.get("id")]
                        except Exception as e:
                            errs.append(f"🔴 {t} - VALIDATION REPORT ERROR: ID field not found in JSON for llm_judge")
                            continue
                    
                    if re.fullmatch(r"\[assistant_(nemo|qwen)_\d+_human_report\]", t):
                        passed = [x for x in results if x["status"].lower() == "passed"]
                        failed = [x for x in results if x["status"].lower() == "failed"]
                        
                        try:
                            hllm_results = [f"{_.get('id')}_Passed" for _ in passed if "llm_judge_" in _.get("id")] + \
                                         [f"{_.get('id')}_Failed" for _ in failed if "llm_judge_" in _.get("id")]
                        except Exception as e:
                            errs.append(f"🔴 {t} - HUMAN REPORT ERROR: ID field not found in JSON for llm_judge")
                            continue
                        
                        report = evaluate_results(results)
                        basic_validation[t] = report
                        
                        if report["total_length"] != basic_validation["[turn_metadata]"]["llm_judge"]:
                            errs.append(f"🔴 {t} - Turn_MetaData Human Report Coverage Error")
                    
                    if llm_results is not None and hllm_results is not None:
                        llmr = set(sorted(llm_results))
                        hllmr = set(sorted(hllm_results))
                        
                        intersection = llmr & hllmr
                        human_validation_passed = len(intersection) == len(llm_results)
                        
                        if not human_validation_passed:
                            validation_results = basic_validation[t.replace("human", "validation")]
                            human_results = basic_validation[t]
                            
                            diff_llm_failed = validation_results["failed"] + (human_results["llm_failed"] - validation_results["llm_failed"])
                            total_failed_percentage = (diff_llm_failed / validation_results["total_length"]) * 100
                            
                            if int(total_failed_percentage) < int(MIN_FAIL_PERCENTAGE):
                                errs.append(f"🔴 {t} - HUMAN VALIDATION ERROR: Total failed percentage ({total_failed_percentage:.2f}%) is less than minimum ({MIN_FAIL_PERCENTAGE}%)")
            
            except Exception as e:
                errs.append(f"🔴 {t} - Error processing: {str(e)}")
    
    return errs

def validate(tags: List[str], previews: List[str], bodies: List[str], indices: List[int], meta: Dict) -> List[str]:
    """Main validation function"""
    errs = []
    errs.extend(validate_structure(tags, previews, indices))
    errs.extend(validate_lengths(tags, bodies, meta))
    errs.extend(validate_json_cells(tags, bodies, previews, indices))
    errs.extend(validate_report_len_cells(tags, bodies, previews, indices))
    return errs

def validate_notebook(filepath: str) -> Tuple[bool, List[str]]:
    """Validate a single notebook file"""
    try:
        tags, cell_errors, previews, bodies, indices, meta = get_tags(filepath)
        all_errs = cell_errors + validate(tags, previews, bodies, indices, meta)
        
        is_valid = len(all_errs) == 0
        return is_valid, all_errs
    except Exception as e:
        return False, [f"🔥 Unexpected error: {str(e)}\n{traceback.format_exc()}"]

# Streamlit App
def main():
    st.set_page_config(
        page_title="Jupyter Notebook Validator",
        page_icon="📓",
        layout="wide"
    )
    
    st.title("📓 Jupyter Notebook Structure Validator")
    st.markdown("---")
    
    # Sidebar for configuration
    with st.sidebar:
        st.header("⚙️ Configuration")
        st.markdown(f"**Min Turn Metadata:** {MIN_TURN_METADATA}")
        st.markdown(f"**Min Fail Percentage:** {MIN_FAIL_PERCENTAGE}%")
        st.markdown(f"**Min Human Pass:** {MIN_HUMAN_PASS_PERCENTAGE}%")
        
        st.markdown("---")
        st.subheader("📋 Validation Checks")
        st.markdown("""
        - ✅ Notebook structure
        - ✅ Required tags
        - ✅ JSON formatting
        - ✅ Turn metadata
        - ✅ Validation reports
        - ✅ Human reports
        - ✅ Length constraints
        - ✅ Restricted instructions
        """)
    
    # Main content
    st.header("Upload Notebook(s)")
    uploaded_files = st.file_uploader(
        "Choose .ipynb file(s)",
        type=["ipynb"],
        accept_multiple_files=True,
        help="Upload one or more Jupyter notebook files for validation"
    )
    
    if uploaded_files:
        st.markdown("---")
        st.header("Validation Results")
        
        # Summary metrics
        col1, col2, col3 = st.columns(3)
        valid_count = 0
        invalid_count = 0
        error_count = 0
        
        results = []
        
        # Process each file
        for uploaded_file in uploaded_files:
            # Create temporary file
            with tempfile.NamedTemporaryFile(delete=False, suffix=".ipynb") as tmp_file:
                tmp_file.write(uploaded_file.getvalue())
                tmp_path = tmp_file.name
            
            try:
                is_valid, errors = validate_notebook(tmp_path)
                
                if is_valid:
                    valid_count += 1
                elif errors and any("Unexpected error" in err for err in errors):
                    error_count += 1
                else:
                    invalid_count += 1
                
                results.append({
                    "filename": uploaded_file.name,
                    "is_valid": is_valid,
                    "errors": errors
                })
            finally:
                # Cleanup temp file
                os.unlink(tmp_path)
        
        # Display summary
        col1.metric("✅ Valid", valid_count)
        col2.metric("❌ Invalid", invalid_count)
        col3.metric("🔥 Errors", error_count)
        
        st.markdown("---")
        
        # Display detailed results
        for result in results:
            filename = result["filename"]
            is_valid = result["is_valid"]
            errors = result["errors"]
            
            if is_valid:
                st.success(f"✅ **{filename}** - VALID")
            else:
                if errors and any("Unexpected error" in err for err in errors):
                    st.error(f"🔥 **{filename}** - CRASH")
                else:
                    st.error(f"❌ **{filename}** - INVALID")
                
                # Display errors in expander
                with st.expander(f"View errors for {filename}", expanded=False):
                    for error in errors:
                        st.code(error, language=None)
        
        # Download results option
        st.markdown("---")
        st.subheader("📥 Download Results")
        
        # Create results text
        results_text = "JUPYTER NOTEBOOK VALIDATION RESULTS\n"
        results_text += "=" * 80 + "\n\n"
        results_text += f"Total Files: {len(uploaded_files)}\n"
        results_text += f"Valid: {valid_count}\n"
        results_text += f"Invalid: {invalid_count}\n"
        results_text += f"Errors: {error_count}\n\n"
        results_text += "=" * 80 + "\n\n"
        
        for result in results:
            filename = result["filename"]
            is_valid = result["is_valid"]
            errors = result["errors"]
            
            if is_valid:
                results_text += f"✅ VALID: {filename}\n\n"
            else:
                results_text += f"❌ INVALID: {filename}\n"
                for error in errors:
                    results_text += f"   {error}\n"
                results_text += "\n"
            results_text += "-" * 80 + "\n\n"
        
        st.download_button(
            label="Download Validation Report",
            data=results_text,
            file_name="validation_report.txt",
            mime="text/plain"
        )
    
    else:
        st.info("👆 Upload one or more .ipynb files to begin validation")
        
        # Display example
        with st.expander("ℹ️ What does this validator check?"):
            st.markdown("""
            ### Validation Checks:
            
            1. **Structure Validation**
               - Presence of required tags: `[system]`, `[turn_metadata]`, `[conversation_end]`
               - Proper alternating pattern of `[user]` and `[assistant_nemo]`/`[assistant_qwen]` tags
            
            2. **JSON Formatting**
               - Valid JSON in `[turn_metadata]`, validation reports, and human reports
               - Proper markdown code block formatting with ` ```json `
            
            3. **Turn Metadata**
               - Minimum number of turn metadata entries
               - No restricted instructions
            
            4. **Validation Reports**
               - Coverage matches turn metadata
               - Proper pass/fail percentages
            
            5. **Human Reports**
               - Validation alignment with automated reports
               - Minimum failure percentage requirements
            
            6. **Length Constraints**
               - Conversation turn count within specified range
               - System prompt word count validation
               - User prompt word count validation
            """)

if __name__ == "__main__":
    main()
