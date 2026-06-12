import os
import html
import requests

from typing import List
from PIL import Image

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import google.generativeai as genai

from reportlab.pdfgen import canvas
from reportlab.lib.utils import simpleSplit

from playwright.sync_api import sync_playwright


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel("gemini-2.5-flash")


app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ResumeRequest(BaseModel):
    github: str
    company: str
    role: str
    linkedin_data: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""


@app.get("/")
def home():
    return {
        "message": "ResumeForge Backend Running"
    }


@app.post("/generate")
def generate_resume(data: ResumeRequest):

    try:
        github_url = data.github.strip()

        username = github_url.rstrip("/").split("/")[-1]

        print("GitHub Username:", username)

        github_warning = ""

        github_data = {}
        repos = []

        if username:
            url = f"https://api.github.com/users/{username}"
            github_response = requests.get(url, timeout=10)

            if github_response.status_code == 200:
                github_data = github_response.json()
            else:
                github_warning = "GitHub profile could not be analyzed properly."

            repos_url = f"https://api.github.com/users/{username}/repos"
            repos_response = requests.get(repos_url, timeout=10)

            if repos_response.status_code == 200:
                repos = repos_response.json()

                if not isinstance(repos, list):
                    repos = []
                    github_warning = "GitHub repositories could not be analyzed properly."

                elif len(repos) == 0:
                    github_warning = "No repositories found. Resume will be generated using provided information."

            else:
                github_warning = "GitHub repositories could not be fetched."

        else:
            github_warning = "No GitHub URL provided. Resume will be generated using provided information."

        print("\nRepositories:")

        skills = set()
        github_summary = ""

        for repo in repos:

            repo_name = repo.get("name")
            repo_description = repo.get("description")
            repo_language = repo.get("language")

            print("\nRepository")
            print("Name:", repo_name)
            print("Description:", repo_description)
            print("Language:", repo_language)
            print("----------------")

            if repo_language:
                skills.add(repo_language)

            github_summary += (
                f"Repository: {repo_name}\n"
                f"Description: {repo_description}\n"
                f"Language: {repo_language}\n\n"
            )

        print("\nSkills Found:")

        for skill in skills:
            print("-", skill)

        skills_text = ", ".join(skills)

        print("Skills:", skills_text)

        prompt = f"""
Create a professional ATS-optimized resume.

Target Company: {data.company}
Target Role: {data.role}

Detected Skills:
{skills_text}

GitHub Profile:

Name: {github_data.get("name")}
Bio: {github_data.get("bio")}

Repositories:

{github_summary}

Use the GitHub repositories to infer projects, technologies, and skills.

LinkedIn Profile Information:

{data.linkedin_data}

Use the LinkedIn data to infer experience, education, and skills.
Analyze the LinkedIn data in combination with the GitHub data to create a professional ATS resume.

Use this exact format:

PROFESSIONAL SUMMARY:
Write a concise 3-4 line summary.

TECHNICAL SKILLS:
- Skill 1
- Skill 2
- Skill 3

PROJECTS:
Project Name
- Description
- Impact

EXPERIENCE:
- Relevant experience points

EDUCATION:
- Degree
- Institution

Rules:
- Maximum 1 page
- Professional tone
- ATS friendly
- Use bullet points
- No unnecessary text
- Return only the resume
- Make it concise, impactful, and short.
- Do not invent projects.
- Only use information available from GitHub and LinkedIn data.
- Do NOT use markdown.
- Do NOT use #.
- Do NOT use **.
- Use plain text section headings only.
"""

        response = model.generate_content(prompt)

        resume_text = response.text

        with open("latest_resume.txt", "w", encoding="utf-8") as f:
            f.write(resume_text)

        with open("latest_contact.txt", "w", encoding="utf-8") as f:
            f.write(f"{data.email}\n")
            f.write(f"{data.phone}\n")
            f.write(f"{data.location}\n")

        return {
            "success": True,
            "resume": resume_text,
            "github_analysis": {
                "username": username,
                "repo_count": len(repos),
                "skills": list(skills),
                "bio": github_data.get("bio") or "Not Available",
                "warning": github_warning
            }
        }

    except Exception as e:
        print("Generate Error:", str(e))

        return {
            "success": False,
            "error": str(e)
        }


@app.post("/upload-linkedin")
async def upload_linkedin(
    images: List[UploadFile] = File(...)
):

    try:
        images_for_gemini = []

        for image in images:

            contents = await image.read()

            temp_filename = f"linkedin_{image.filename}"

            with open(temp_filename, "wb") as f:
                f.write(contents)

            print("Saved:", temp_filename)

            with Image.open(temp_filename) as img:
                images_for_gemini.append(
                    img.convert("RGB").copy()
                )

        prompt = """
Analyze all LinkedIn profile screenshots.

Combine information from all screenshots.

Extract:

- Full Name
- Professional Headline
- Education
- Experience
- Skills

Return clean structured text.
"""

        response = model.generate_content(
            [prompt] + images_for_gemini
        )

        linkedin_data = response.text

        print(linkedin_data)

        return {
            "success": True,
            "linkedin_data": linkedin_data
        }

    except Exception as e:
        print("LinkedIn Upload Error:", str(e))

        return {
            "success": False,
            "error": str(e)
        }


@app.get("/download-pdf")
def download_pdf():

    pdf_file = "resume.pdf"

    c = canvas.Canvas(pdf_file)

    c.setFont("Helvetica-Bold", 24)

    c.drawString(
        50,
        800,
        "AI GENERATED RESUME"
    )

    c.setFont("Helvetica", 11)

    c.drawString(
        50,
        780,
        "Created with ResumeForge AI"
    )

    c.line(
        50,
        770,
        550,
        770
    )

    c.setFont("Helvetica", 11)

    y = 740

    try:
        with open("latest_resume.txt", "r", encoding="utf-8") as f:
            lines = f.readlines()

        for line in lines:

            text = line.strip()

            wrapped_lines = simpleSplit(
                text,
                "Helvetica",
                11,
                470
            )

            for wrapped_line in wrapped_lines:
                c.drawString(60, y, wrapped_line)
                y -= 18

                if y < 50:
                    c.showPage()
                    c.setFont("Helvetica", 11)
                    y = 800

    except Exception as e:
        c.drawString(
            50,
            740,
            f"Error: {str(e)}"
        )

    c.save()

    return FileResponse(
        pdf_file,
        media_type="application/pdf",
        filename="ResumeForge_Resume.pdf"
    )


def clean_text(text):
    return html.escape(text or "").replace("\n", "<br>")


def split_resume_sections(resume_text):
    sections = {
        "summary": "",
        "skills": "",
        "experience": "",
        "projects": "",
        "education": ""
    }

    current_section = None

    headings = {
        "PROFESSIONAL SUMMARY": "summary",
        "SUMMARY": "summary",
        "TECHNICAL SKILLS": "skills",
        "SKILLS": "skills",
        "EXPERIENCE": "experience",
        "PROJECTS": "projects",
        "EDUCATION": "education"
    }

    for line in resume_text.splitlines():

        clean_line = line.strip().replace(":", "").replace("*", "").replace("#", "")

        upper_line = clean_line.upper()

        if upper_line in headings:
            current_section = headings[upper_line]
            continue

        if current_section:
            sections[current_section] += line + "\n"

    return sections


def format_bullets(text):
    output = ""

    for line in text.splitlines():

        line = line.strip()

        if not line:
            continue

        if line.startswith("-"):
            output += f"<li>{html.escape(line[1:].strip())}</li>"
        else:
            output += f"<p>{html.escape(line)}</p>"

    return output


@app.get("/download-professional-pdf")
def download_professional_pdf():

    if not os.path.exists("latest_resume.txt"):
        return {
            "success": False,
            "error": "No resume generated yet."
        }

    with open("latest_resume.txt", "r", encoding="utf-8") as f:
        resume_text = f.read()

    email = "your.email@example.com"
    phone = "+91 XXXXX XXXXX"
    location = "Your City, India"

    if os.path.exists("latest_contact.txt"):

        with open("latest_contact.txt", "r", encoding="utf-8") as f:
            contact_lines = f.read().splitlines()

        if len(contact_lines) > 0 and contact_lines[0]:
            email = contact_lines[0]

        if len(contact_lines) > 1 and contact_lines[1]:
            phone = contact_lines[1]

        if len(contact_lines) > 2 and contact_lines[2]:
            location = contact_lines[2]

    sections = split_resume_sections(resume_text)

    summary = clean_text(sections["summary"])
    skills = format_bullets(sections["skills"])
    experience = format_bullets(sections["experience"])
    projects = format_bullets(sections["projects"])
    education = clean_text(sections["education"])

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">

        <style>
            @page {{
    size: A4;
    margin: 0;
}}

* {{
    box-sizing: border-box;
}}

html,
body {{
    margin: 0;
    padding: 0;
    font-family: Arial, sans-serif;
    background: white;
    color: #222;
}}

.resume-page {{
    width: 210mm;
    min-height: 297mm;
    height: auto;

    display: flex;
    align-items: stretch;

    background: white;

    padding: 18mm 16mm;

    border: 10mm solid #9b918c;

    position: relative;

    overflow: visible;
}}

.resume-page::before {{
    content: "";
    position: absolute;
    top: -10mm;
    right: -10mm;
    width: 0;
    height: 0;
    border-top: 16mm solid #7f7773;
    border-left: 16mm solid transparent;
}}

.resume-page::after {{
    content: "";
    position: absolute;
    bottom: -10mm;
    left: -10mm;
    width: 0;
    height: 0;
    border-bottom: 16mm solid #7f7773;
    border-right: 16mm solid transparent;
}}

.sidebar {{
    width: 34%;
    padding-right: 16px;
    border-right: 2px solid #b7aaa5;
    font-size: 11px;
}}

.main {{
    width: 66%;
    padding-left: 24px;
}}

.contact {{
    font-size: 10.5px;
    line-height: 1.6;
    margin-bottom: 18px;
    color: #222;
}}

.name {{
    font-size: 30px;
    font-weight: 300;
    letter-spacing: 1px;
    color: #555;
    margin-bottom: 22px;
}}

.section-title {{
    font-size: 15px;
    color: #555;
    margin-top: 18px;
    margin-bottom: 7px;
    font-weight: 600;
    page-break-after: avoid;
}}

.sidebar-title {{
    font-size: 13.5px;
    color: #555;
    margin-top: 20px;
    margin-bottom: 7px;
    font-weight: 600;
    page-break-after: avoid;
}}

p {{
    margin: 0 0 7px 0;
    line-height: 1.35;
    font-size: 11px;
}}

ul {{
    margin: 0;
    padding-left: 15px;
}}

li {{
    margin-bottom: 4px;
    line-height: 1.35;
    font-size: 11px;
}}

.skill-list li {{
    font-size: 10.5px;
}}

.section-block {{
    page-break-inside: avoid;
}}

.experience-item {{
    page-break-inside: avoid;
    margin-bottom: 10px;
}}

@media print {{
    body {{
        background: white;
    }}

    .resume-page {{
        page-break-after: auto;
    }}
}}
        </style>
    </head>

    <body>

        <div class="resume-page">

            <div class="sidebar">

                <div class="contact">
                    <strong>{email}</strong><br>
                    {phone}<br>
                    {location}
                </div>

                <div class="sidebar-title">Skills</div>
                <ul class="skill-list">
                    {skills}
                </ul>

                <div class="sidebar-title">Education And Training</div>
                <p>{education}</p>

                <div class="sidebar-title">Languages</div>
                <p>English: Professional</p>
                <p>Hindi: Native</p>

                <div class="sidebar-title">Interests And Hobbies</div>
                <ul>
                    <li>Technology</li>
                    <li>Software Development</li>
                    <li>Problem Solving</li>
                </ul>

            </div>

            <div class="main">

                <div class="name">ResumeForge Candidate</div>

                <div class="section-title">Summary</div>
                <p>{summary}</p>

                <div class="section-title">Experience</div>
                <ul>
                    {experience}
                </ul>

                <div class="section-title">Projects</div>
                <ul>
                    {projects}
                </ul>

            </div>

        </div>

    </body>
    </html>
    """

    html_file = "professional_resume.html"
    pdf_file = "professional_resume.pdf"

    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    with sync_playwright() as p:

        browser = p.chromium.launch()

        page = browser.new_page()

        page.goto(
            "file://" + os.path.abspath(html_file),
            wait_until="networkidle"
        )

        page.pdf(
    path=pdf_file,
    format="A4",
    print_background=True,
    prefer_css_page_size=True,
    margin={
        "top": "0",
        "right": "0",
        "bottom": "0",
        "left": "0"
    }
)

        browser.close()

    return FileResponse(
        pdf_file,
        media_type="application/pdf",
        filename="ResumeForge_Professional_Template.pdf"
    )