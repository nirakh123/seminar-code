import os
import fitz  # PyMuPDF
from flask import Flask, request, render_template, send_from_directory, send_file
from werkzeug.utils import secure_filename
import google.generativeai as genai
from base64 import b64encode
# from dotenv import load_dotenv  # No longer needed
import uuid
import re
import io
import itertools  # For cycling through API keys

app = Flask(__name__)

# Directly define your API keys here
API_KEYS = [
    "AIzaSyDbd2fZKX-OloZiLWGXwwAPyWzS5DQ0bfc",
    "AIzaSyCrCTkWBRzdVtR_rgRn_CkUH-blkUff2N4",
    "AIzaSyDDQ9AARIDhwlh7b0Z5EyeyiFKUeQ7nXX8"
]
api_key_cycle = itertools.cycle(API_KEYS)
current_api_key = next(api_key_cycle)

# # Load environment variables - NO LONGER NEEDED
# load_dotenv()
# API_KEY = os.getenv("GEMINI_API_KEY")
# if not API_KEY:
#     raise ValueError("GEMINI_API_KEY not found in .env file!")

genai.configure(api_key=current_api_key)

UPLOAD_FOLDER = "static"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

def extract_figure_references(text):
    """Extract lines with 'Figure', 'Fig.', etc."""
    figure_references = []
    if text:
        lines = text.splitlines()
        for line in lines:
            if re.search(r"\b(fig(?:ure)?\.?\s*\d+)", line, re.IGNORECASE):
                figure_references.append(line.strip())
    return figure_references

def pdf_to_images(pdf_path, output_folder):
    image_data_list = []
    pdf_document = None
    try:
        pdf_document = fitz.open(pdf_path)
        if not os.path.exists(output_folder):
            os.makedirs(output_folder)

        for page_num in range(len(pdf_document)):
            page = pdf_document[page_num]
            pix = page.get_pixmap(dpi=300)
            output_file = os.path.join(output_folder, f"page_{page_num + 1}.png")
            pix.save(output_file)

            page_text = page.get_text("text")
            print(f"\n--- Page {page_num + 1} Text ---\n{page_text}\n")  # DEBUG

            image_data_list.append({
                "image_path": output_file,
                "page_text": page_text,
                "page_number": page_num + 1
            })

        return image_data_list
    except Exception as e:
        print(f"An error occurred: {str(e)}")
        return []
    finally:
        if pdf_document is not None:
            pdf_document.close()

def generate_alt_text(image_path, page_text=None):
    global current_api_key
    try:
        with open(image_path, "rb") as image_file:
            image_data = image_file.read()
            image_base64 = b64encode(image_data).decode("utf-8")

        # Extract figure references
        figure_references = extract_figure_references(page_text)
        print(f"\n--- Analyzing image {image_path} ---")
        print(f"Extracted References:\n{figure_references}\n")  # DEBUG

        reference_context = "\n".join(figure_references) if figure_references else ""

        prompt = (
            "Identify and describe only the figures that are actually depicted in the image, such as graphs, charts, diagrams, "
            "illustrations, or photographs. Ignore any textual mentions of figures that are not visually present. "
            "Do not describe tables, which are structured data presentations with rows and columns. "
            "List each figure separately with 'Figure X:' followed by its description "
            "(e.g., 'Figure 1: A line graph showing...'). If there are no figures visually present in the image, return "
            "'No figures present in the image.'"
        )

        if reference_context:
            prompt = f"The following text appears near this image and may describe or reference it:\n\n{reference_context}\n\n{prompt}"

        print(f"--- Prompt sent to Gemini for image {image_path} ---\n{prompt}\n")  # DEBUG

        model = genai.GenerativeModel("gemini-1.5-flash")
        response = model.generate_content([
            {"mime_type": "image/png", "data": image_base64},
            {"text": prompt}
        ])

        alt_text = response.text.strip()
        if alt_text == "No figures present in the image.":
            return [alt_text]

        figures = []
        for line in alt_text.split("\n"):
            line = line.strip()
            if line.startswith("Figure ") and ":" in line:
                figures.append(line)
        return figures if figures else ["No figures present in the image."]
    except Exception as e:
        print(f"Error generating alt text: {str(e)}")
        # Try the next API key if the current one fails
        current_api_key = next(api_key_cycle)
        genai.configure(api_key=current_api_key)
        return [f"Error generating alt text, trying another key: {str(e)}"]

@app.route("/", methods=["GET", "POST"])
def upload_file():
    if request.method == "POST":
        if "file" not in request.files:
            return render_template("upload.html", error="No file part")
        file = request.files["file"]
        if file.filename == "":
            return render_template("upload.html", error="No file selected")
        if file and file.filename.lower().endswith(".pdf"):
            filename = secure_filename(file.filename)
            pdf_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
            file.save(pdf_path)

            session_id = str(uuid.uuid4())
            output_folder = os.path.join(app.config["UPLOAD_FOLDER"], "extracted_pages", session_id)
            image_data_list = pdf_to_images(pdf_path, output_folder)
            results = []
            figure_count = 0
            all_alt_texts = []  # Store alt texts for download

            for image_data in image_data_list:
                image_path = image_data["image_path"]
                page_text = image_data["page_text"]
                alt_texts = generate_alt_text(image_path, page_text)
                relative_path = os.path.relpath(image_path, app.config["UPLOAD_FOLDER"])
                results.append({"path": relative_path, "alt_texts": alt_texts})
                all_alt_texts.append((image_data["page_number"], alt_texts))  # Store page number and alt texts
                for alt_text in alt_texts:
                    if not alt_text.startswith("No figures") and not alt_text.startswith("Error"):
                        figure_count += 1

            os.remove(pdf_path)  # Cleanup

            # Store all_alt_texts in app config for access in download route
            app.config[f"alt_texts_{session_id}"] = all_alt_texts

            return render_template("results.html", images=results, figure_count=figure_count, session_id=session_id)
        return render_template("upload.html", error="Please upload a PDF file")
    return render_template("upload.html")

@app.route("/static/<path:filename>")
def static_files(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

@app.route("/download_alt_texts/<session_id>")
def download_alt_texts(session_id):
    all_alt_texts = app.config.get(f"alt_texts_{session_id}", [])
    if not all_alt_texts:
        return "No alt texts found for this session.", 404

    # Create a string with all alt texts, formatted with proper spacing
    content = []
    for page_number, alt_texts in all_alt_texts:
        content.append(f"Page {page_number}:")
        for alt_text in alt_texts:
            content.append(alt_text)
        content.append("")  # Empty line between pages

    # Convert to a single string
    content_str = "\n".join(content)

    # Create a file-like object
    txt_file = io.StringIO(content_str)
    txt_file.seek(0)

    # Serve the file as a download
    return send_file(
        io.BytesIO(txt_file.getvalue().encode("utf-8")),
        mimetype="text/plain",
        as_attachment=True,
        download_name="alt_texts.txt"
    )