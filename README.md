# Jupyter Notebook Validator - Streamlit App

A web-based application for validating Jupyter notebook structure, formatting, and content compliance.

## Features

- 📤 **Easy Upload**: Drag and drop multiple `.ipynb` files
- ✅ **Comprehensive Validation**: 
  - Structure validation (tag sequencing)
  - Format validation (tag formatting and spacing)
  - Length validation (conversation and prompt lengths)
  - JSON validation (syntax in metadata/reports)
  - Model family consistency (nemo vs qwen)
- 📊 **Visual Feedback**: Color-coded errors with detailed messages
- 📋 **Metadata Display**: View notebook metadata
- 🔍 **Detailed Error Reports**: Cell-level error tracking with snippets

## Installation

1. Install the required dependencies:
```bash
pip install -r requirements.txt
```

## Usage

1. Run the Streamlit app:
```bash
streamlit run notebook_validator_app.py
```

2. Open your browser (it should open automatically) to the URL shown in the terminal (typically `http://localhost:8501`)

3. Upload one or more `.ipynb` files using the file uploader

4. View validation results instantly

## Validation Rules

### Tag Structure
The validator checks for proper tag sequencing:
- `[system]` - System prompts
- `[user]` - User messages
- `[thinking]` - Internal reasoning
- `[assistant]` - Assistant responses
- `[turn_metadata]` - Turn metadata (must be before final thinking/assistant)
- `[assistant_nemo_#]` or `[assistant_qwen_#]` - Model-specific responses
- `[assistant_X_#_validation_report]` - Validation reports
- `[assistant_X_#_human_report]` - Human evaluation reports

### Format Requirements
- Each tag must be in the format `**[tag_name]**`
- Tag must be followed by exactly one blank line
- Content starts after the blank line

### Model Family Rules
- Only ONE model family (nemo OR qwen) per notebook
- Model blocks must follow the pattern:
  - `[thinking]` → `[assistant_X_#]` → `[validation_report]` → `[human_report]`
- Sequential numbering (1, 2, 3, ...)

### JSON Requirements
- `[turn_metadata]`, validation reports, and human reports must contain valid JSON
- JSON must be wrapped in ```json code blocks

### Length Validation
Based on metadata in the first cell:
- Conversation length (number of turns)
- System prompt length (word count)
- User prompt length (average word count)

## Example Notebook Structure

```
Cell 0: (Metadata - no tag required)
- Conversation length: 3-5
- System prompt length: 100-200
- User prompt length: 50-100

Cell 1:
**[system]**

Your system prompt here...

Cell 2:
**[user]**

User question here...

Cell 3:
**[thinking]**

Internal reasoning...

Cell 4:
**[assistant]**

Assistant response...

Cell 5:
**[turn_metadata]**

```json
{
  "turn_data": "..."
}
```

Cell 6:
**[thinking]**

Model comparison reasoning...

Cell 7:
**[assistant_nemo_1]**

First model response...

Cell 8:
**[assistant_nemo_1_validation_report]**

```json
{
  "validation": "..."
}
```

Cell 9:
**[assistant_nemo_1_human_report]**

```json
{
  "human_eval": "..."
}
```
```

## Error Types

### 🔴 Format Errors
- Missing tag headers
- Incorrect blank line spacing
- Malformed tag syntax

### 🟠 Structure Errors
- Invalid tag sequencing
- Missing required tags
- Incomplete model blocks
- Mixed model families

### 🟡 Length Errors
- Conversation turns outside specified range
- System prompt word count violations
- User prompt average length violations

### 🔵 JSON Errors
- Missing ```json code blocks
- Invalid JSON syntax
- Malformed JSON structure

## Tips

1. **Batch Validation**: Upload multiple notebooks at once for efficient validation
2. **Error Navigation**: Use the expanders to view detailed error information
3. **Metadata Verification**: Check the metadata section to ensure constraints are properly defined
4. **Quick Fixes**: Error snippets help you quickly locate and fix issues

## Troubleshooting

**App won't start:**
- Ensure streamlit is installed: `pip install streamlit`
- Check Python version (3.8+ recommended)

**Upload fails:**
- Verify file is valid JSON
- Check file extension is `.ipynb`
- Ensure file isn't corrupted

**False positives:**
- Check metadata format in Cell 0
- Verify tag format exactly matches `**[tag]**`
- Ensure blank line after each tag

## Support

For issues or questions, refer to the error messages which provide:
- Cell number where error occurred
- Tag that caused the issue
- Specific error description
- Content snippet for context
