from email.mime import image
from urllib import response

from click import prompt
from reportlab.pdfgen import canvas
from fastapi.responses import FileResponse
from reportlab.lib.utils import simpleSplit
from fastapi import UploadFile, File
from typing import List
from PIL import Image
import google.generativeai as genai
import textwrap
import requests
import html
import os
from playwright.sync_api import sync_playwright
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel("gemini-2.5-flash")

from fastapi import FastAPI
from pydantic import BaseModel

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

    github_url = data.github

    username = github_url.rstrip("/").split("/")[-1]

    print("GitHub Username:", username)

    url = f"https://api.github.com/users/{username}"

    response = requests.get(url)

    github_data = response.json()
    
    repos_url = f"https://api.github.com/users/{username}/repos"

    repos_response = requests.get(repos_url)

    repos = repos_response.json()
    

    print("\nRepositories:")

    skills = set()

    github_summary = ""

    for repo in repos:

        print("\nRepository")

        print("Name:", repo.get("name"))

        print("Description:", repo.get("description"))

        print("Language:", repo.get("language"))

        language = repo.get("language")

    if language:
        skills.add(language)

        print("----------------")

        github_summary += (
        f"Repository: {repo.get('name')}\n"
        f"Description: {repo.get('description')}\n"
        f"Language: {repo.get('language')}\n\n"
        )
        print(github_summary)

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

Use the GitHub repositories to infer projects,
technologies and skills.

LinkedIn Profile Information:

{data.linkedin_data}
Use the linkedIn data to infer experience, education and skills. And make the resume according to that linkedin data. 
Analyze the LinkedIn data 
in combination with the GitHub data to create a professional ATS resume.

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
Use the GitHub repositories to identify:

- Projects
- Technologies
- Skills

Do not invent projects.
Only use information available from GitHub data.

    Rules:
    - Maximum 1 page
    - Professional tone
    - ATS friendly
    - Use bullet points
    - No unnecessary text
    - Return only the resume
    - Make It concise and impactful and short.
    Use this exact format.

    Do NOT use markdown.
    Do NOT use #.
    Do NOT use **.
    Use plain text section headings only.
    """
    try:
        response = model.generate_content(prompt)
        with open("latest_resume.txt", "w", encoding="utf-8") as f:
            f.write(response.text)

            with open("latest_contact.txt", "w", encoding="utf-8") as f:
             f.write(f"{data.email}\n")
            f.write(f"{data.phone}\n")
            f.write(f"{data.location}\n")

            return {
    "success": True,
    "resume": response.text,

    "github_analysis": {
        "username": username,
        "repo_count": len(repos),
        "skills": list(skills),
        "bio": github_data.get("bio") or "Not Available"
    }
}

    except Exception as e:
            return {
            "success": False,
            "error": str(e)
        }
    
@app.get("/download-pdf")
def download_pdf():

    pdf_file = "resume.pdf"

    c = canvas.Canvas(pdf_file)

    c.setFont("Helvetica-Bold", 20)
    c.setFillColorRGB(0.49, 0.23, 0.93)

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

    c.line(50, 790, 550, 790)

    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 11)

    y = 760

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
                break

    except Exception as e:

        c.drawString(
            50,
            800,
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

            body {{
                margin: 0;
                padding: 0;
                font-family: Arial, sans-serif;
                background: #9b918c;
                color: #222;
            }}

            .resume-page {{
                width: 210mm;
                height: 297mm;
                display: flex;
                background: white;
                box-sizing: border-box;
                padding: 18mm 16mm;
                border: 10mm solid #9b918c;
                position: relative;
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
                width: 35%;
                padding-right: 18px;
                border-right: 2px solid #b7aaa5;
                box-sizing: border-box;
                font-size: 12px;
            }}

            .main {{
                width: 65%;
                padding-left: 28px;
                box-sizing: border-box;
            }}

            .contact {{
                font-size: 11px;
                line-height: 1.7;
                margin-bottom: 22px;
                color: #222;
            }}

            .name {{
                font-size: 34px;
                font-weight: 300;
                letter-spacing: 1px;
                color: #555;
                margin-bottom: 28px;
            }}

            .section-title {{
                font-size: 17px;
                color: #555;
                margin-top: 22px;
                margin-bottom: 8px;
                font-weight: 500;
            }}

            .sidebar-title {{
                font-size: 15px;
                color: #555;
                margin-top: 24px;
                margin-bottom: 8px;
                font-weight: 500;
            }}

            p {{
                margin: 0 0 8px 0;
                line-height: 1.45;
                font-size: 12px;
            }}

            ul {{
                margin: 0;
                padding-left: 16px;
            }}

            li {{
                margin-bottom: 5px;
                line-height: 1.4;
                font-size: 12px;
            }}

            .skill-list li {{
                font-size: 11.5px;
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
            print_background=True
        )

        browser.close()

    return FileResponse(
        pdf_file,
        media_type="application/pdf",
        filename="ResumeForge_Professional_Template.pdf"
    )
@app.post("/upload-linkedin")
async def upload_linkedin(
    images: List[UploadFile] = File(...)
):

    saved_files = []

    for i, image in enumerate(images):

        contents = await image.read()

        filename = f"linkedin_{i}.png"

        with open(filename, "wb") as f:
            f.write(contents)

        saved_files.append(filename)

        print("Saved:", filename)

    images_for_gemini = []
    saved_files = []
    for file in saved_files:
        with Image.open(file) as img:
            images_for_gemini.append(img.convert("RGB").copy())


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


    return {
    "success": True,
    "linkedin_data": response.text
    }