import os
import html
import requests
import re

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

        linkedin_name = extract_name_from_linkedin(data.linkedin_data)

        github_name = github_data.get("name") or ""

        candidate_name = (
        linkedin_name
        or github_name
        or username
        or "Candidate Name"
    )

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
You are an expert resume writer and ATS optimization specialist.

Create a detailed, professional, ATS-friendly resume for the following target:

Target Company:
{data.company}

Target Role:
{data.role}

GitHub Profile Analysis:
{github_summary}

Detected GitHub Skills:
{skills_text}

LinkedIn Profile Information:
{data.linkedin_data}

Instructions:

1. Write a strong professional resume, not a short summary.

2. Use this exact structure:

PROFESSIONAL SUMMARY
Write 4-5 detailed lines.
Mention years of experience if available.
Mention target role alignment.
Mention key domains, tools, leadership, and measurable impact.

TECHNICAL SKILLS
Group skills into categories.
Example:
- Programming: Python, JavaScript, TypeScript
- Product/Tools: Product Management, Analytics, GitHub
- Platforms: Cloud, APIs, Databases

EXPERIENCE
For each role, write:
Job Title, Company, Dates, Location if available.
Then write 2-4 strong bullet points.
Each bullet should include action + responsibility + impact.
Use metrics if available.
Do not make bullets too short.

PROJECTS
Create a detailed Projects section using both LinkedIn extracted projects and GitHub repositories.

Rules:
- If LinkedIn Profile Information contains projects, include them.
- If GitHub repositories are available, include the strongest repositories as projects.
- If both LinkedIn projects and GitHub repositories exist, combine them intelligently.
- For each project, write:
  Project Name
  - What the project does
  - Technologies used
  - Impact, purpose, or result
- Do not leave the Projects section empty.
- If no projects are available from either LinkedIn or GitHub, completely omit the Projects section.

EDUCATION
Include education details from LinkedIn if available.

Rules:
- Make the resume detailed but still professional.
- Do not invent fake companies, fake degrees, or fake metrics.
- If exact metrics are missing, describe impact honestly without numbers.
- Use clear bullet points.
- Avoid long paragraphs except in Professional Summary.
- Optimize for ATS keywords related to the target role.
- Do not include markdown tables.
- Do not include extra explanations outside the resume.

If the user uploaded LinkedIn screenshots containing projects, those projects must be included in the final resume.
"""

        response = model.generate_content(prompt)

        resume_text = response.text

        with open("latest_resume.txt", "w", encoding="utf-8") as f:
            f.write(resume_text)

        with open("latest_contact.txt", "w", encoding="utf-8") as f:
            f.write(f"{candidate_name}\n")
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
def extract_name_from_linkedin(linkedin_data):
    if not linkedin_data:
        return ""

    patterns = [
        r"Full Name\s*:\s*(.+)",
        r"Name\s*:\s*(.+)",
        r"- Full Name\s*:\s*(.+)",
        r"- Name\s*:\s*(.+)"
    ]

    for pattern in patterns:
        match = re.search(pattern, linkedin_data, re.IGNORECASE)

        if match:
            name = match.group(1).strip()

            name = name.replace("*", "").replace("#", "").strip()

            if name and len(name) < 80:
                return name

    return ""

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

Extract the following information carefully:

- Full Name
- Professional Headline
- Contact information if visible
- Education
- Experience
- Projects
- Skills
- Certifications if visible

For Projects, extract:
- Project name
- Project description
- Technologies used
- Results, impact, or purpose if visible

Important:
Do not ignore the Projects section if it appears in any screenshot.
If a screenshot contains projects, include them clearly.
If projects are not visible, write: Projects: Not found.

Return clean structured text in this format:

FULL NAME:
...

HEADLINE:
...

EDUCATION:
...

EXPERIENCE:
...

PROJECTS:
- Project Name:
  Description:
  Technologies:
  Impact:

SKILLS:
...
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
        elif line.startswith("•"):
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

    candidate_name = "Candidate Name"
    email = "your.email@example.com"
    phone = "+91 XXXXX XXXXX"
    location = "Your City, India"

    if os.path.exists("latest_contact.txt"):

        with open("latest_contact.txt", "r", encoding="utf-8") as f:
            contact_lines = f.read().splitlines()

    if len(contact_lines) > 0 and contact_lines[0]:
        candidate_name = contact_lines[0]

    if len(contact_lines) > 1 and contact_lines[1]:
        email = contact_lines[1]

    if len(contact_lines) > 2 and contact_lines[2]:
        phone = contact_lines[2]

    if len(contact_lines) > 3 and contact_lines[3]:
        location = contact_lines[3]

    sections = split_resume_sections(resume_text)

    summary = clean_text(sections["summary"])
    skills = format_bullets(sections["skills"])
    experience = format_bullets(sections["experience"])
    projects = format_bullets(sections["projects"])
    projects_section_html = ""

    if projects.strip():
        projects_section_html = f"""
    <div class="section-title">Projects</div>
    <ul>
        {projects}
    </ul>
    """
    education = clean_text(sections["education"])
    candidate_name_display = html.escape(candidate_name)
    f
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

.page-border {{
    position: fixed;
    top: 0;
    left: 0;

    width: 210mm;
    height: 297mm;

    border: 10mm solid #9b918c;

    z-index: 0;

    pointer-events: none;
}}

.corner-top {{
    position: fixed;
    top: 0;
    right: 0;

    width: 0;
    height: 0;

    border-top: 16mm solid #7f7773;
    border-left: 16mm solid transparent;

    z-index: 1;
}}

.corner-bottom {{
    position: fixed;
    bottom: 0;
    left: 0;

    width: 0;
    height: 0;

    border-bottom: 16mm solid #7f7773;
    border-right: 16mm solid transparent;

    z-index: 1;
}}

.resume-page {{
    width: 210mm;
    min-height: 297mm;

    display: flex;
    align-items: stretch;

    background: white;

    padding: 14mm 12mm;

    position: relative;
    z-index: 2;
}}

.sidebar {{
    width: 32%;
    padding-right: 12px;
    border-right: 2px solid #b7aaa5;
    font-size: 10px;
}}

.main {{
    width: 68%;
    padding-left: 18px;
}}

.contact {{
    font-size: 9.5px;
    line-height: 1.3;
    margin-bottom: 12px;
    color: #222;
}}


.name {{
    font-size: 26px;
    font-weight: 300;
    letter-spacing: 1px;
    color: #555;
    margin-bottom: 14px;
}}

.section-title {{
    font-size: 13.5px;
    color: #555;
    margin-top: 12px;
    margin-bottom: 4px;
    font-weight: 700;
    break-after: avoid;
}}

.sidebar-title {{
    font-size: 12px;
    color: #555;
    margin-top: 13px;
    margin-bottom: 4px;
    font-weight: 700;
    break-after: avoid;
}}

p {{
    margin: 0 0 4px 0;
    line-height: 1.22;
    font-size: 9.8px;
}}

ul {{
    margin: 0;
    padding-left: 12px;
}}

li {{
    margin-bottom: 2.5px;
    line-height: 1.22;
    font-size: 9.8px;
}}

.experience-item {{
    margin-bottom: 6px;
    break-inside: avoid;
}}


.skill-list li {{
    font-size: 9.5px;
    margin-bottom: 2px;
}}

.section-block {{
    break-inside: avoid;
}}
        </style>
    </head>

    <body>
<div class="page-border"></div>
<div class="corner-top"></div>
<div class="corner-bottom"></div>
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

                <div class="name">{candidate_name_display}</div>

                <div class="section-title">Summary</div>
                <p>{summary}</p>

                <div class="section-title">Experience</div>
                <ul>
                    {experience}
                </ul>

                {projects_section_html}

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