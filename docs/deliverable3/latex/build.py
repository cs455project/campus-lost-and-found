"""Build every Deliverable 3 report: Markdown source -> LaTeX body -> PDF (XeLaTeX).

    python build.py            # all reports
    python build.py 01 05      # only these
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# number, source, output name, title, subtitle, running head, table of contents
REPORTS = [
    ("01", "01_test_reports", "TraceBack_D3_01_Test_Reports", "Test Reports",
     "Unit, Integration, System and Adversarial Testing", "Test Reports", True),
    ("02", "02_coverage", "TraceBack_D3_02_Code_Coverage", "Code Coverage Report",
     "Line and Branch Coverage of the Backend", "Code Coverage", False),
    ("03", "03_concurrency", "TraceBack_D3_03_Concurrency", "Concurrency Test Report",
     "Races, Locking and Simultaneous Users", "Concurrency", False),
    ("04", "04_failure", "TraceBack_D3_04_Failure_and_Recovery", "Failure and Recovery Test Report",
     "Fault Injection and Self-Recovery", "Failure and Recovery", False),
    ("05", "05_security", "TraceBack_D3_05_Security_Threat_Model", "Security and Threat Model",
     "STRIDE Analysis, Evidence and Residual Risk", "Security", True),
    ("06", "06_performance", "TraceBack_D3_06_Performance_and_Optimisation",
     "Performance, Load Testing and Optimisation", "Bottlenecks Found, Changes Made, Gains Measured",
     "Performance", True),
    ("07", "07_defects", "TraceBack_D3_07_Defects_Found_and_Fixed", "Defects Found and Fixed",
     "Defect Register and Evidence for Deliverable 3", "Defects", False),
    ("08", "08_deployment", "TraceBack_D3_08_Deployment_Documentation", "Deployment Documentation",
     "AWS EC2, Docker Compose and Caddy", "Deployment", True),
    ("09", "09_costing", "TraceBack_D3_09_Costing", "Costing",
     "Running Cost, Free-Tier Use and Effort Estimate", "Costing", False),
    ("10", "10_timeline", "TraceBack_D3_10_Timeline_and_Gantt", "Timeline and Gantt Chart",
     "From Proposal to a Tested Deployment", "Timeline", False),
    ("11", "11_traceability", "TraceBack_D3_11_Traceability_Matrix",
     "Requirements Traceability Matrix", "Requirements to Jira to GitHub to Testing (Final)",
     "Traceability", True),
    ("12", "12_ai_log", "TraceBack_D3_12_AI_Engineering_Log", "AI Engineering Log",
     "Prompts, AI Output, Evaluation and Decisions", "AI Engineering Log", False),
    ("13", "13_jira_defects", "TraceBack_D3_13_Jira_Defect_History", "Bug and Defect History",
     "Jira Bug Issues, Workflow and Resolution", "Defect History (Jira)", True),
    ("14", "14_jira_dashboard", "TraceBack_D3_14_Jira_Dashboard_and_Project_Report",
     "Final Jira Dashboard and Project Report", "Backlog, Sprints, Delivery and Quality at Close",
     "Jira Dashboard and Project Report", True),
]


def summary(md: str) -> str:
    """The bold paragraph straight after the title, as LaTeX."""
    sys.path.insert(0, str(ROOT))
    from md2tex import inline
    lines = md.splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("**"):
            para = []
            while i < len(lines) and lines[i].strip():
                para.append(lines[i].strip())
                i += 1
            return inline(" ".join(para))
    return ""


def wrapper(num, src, title, subtitle, short, toc):
    md = (ROOT / "md" / f"{src}.md").read_text(encoding="utf-8")
    return "\n".join([
        r"\documentclass[11pt,a4paper]{article}",
        r"\usepackage{traceback-d3}",
        r"\renewcommand{\reportshort}{%s}" % short,
        r"\hypersetup{pdftitle={TraceBack -- %s (CS455 Deliverable 3)}}" % title,
        r"\begin{document}",
        r"\maketitlepage{%d}{%s}{%s}" % (int(num), title, subtitle),
        r"\begin{keybox}{Summary}",
        summary(md),
        r"\end{keybox}",
        r"\vspace{2pt}{\setlength{\parskip}{1pt}\tableofcontents}\newpage" if toc else "",
        r"\input{body/%s}" % src,
        r"\end{document}",
        "",
    ])


def main(selected):
    sys.path.insert(0, str(ROOT))
    from md2tex import convert
    for num, src, out, title, subtitle, short, toc in REPORTS:
        if selected and num not in selected:
            continue
        md_path = ROOT / "md" / f"{src}.md"
        if not md_path.exists():
            print("skip (no source)", src)
            continue
        (ROOT / "body" / f"{src}.tex").write_text(convert(md_path.read_text(encoding="utf-8")), encoding="utf-8")
        (ROOT / f"{out}.tex").write_text(wrapper(num, src, title, subtitle, short, toc), encoding="utf-8")
        for _ in range(2):
            r = subprocess.run(["xelatex", "-interaction=nonstopmode", "-halt-on-error", f"{out}.tex"],
                               cwd=ROOT, capture_output=True, text=True)
        log = (ROOT / f"{out}.log").read_text(encoding="utf-8", errors="replace")
        errors = re.findall(r"^! .*", log, re.M)
        overfull = len(re.findall(r"Overfull \\hbox \((\d+\.\d+)pt", log))
        missing = re.findall(r"Missing character: There is no (.)", log)
        pages = re.search(r"Output written on .*\((\d+) pages", log)
        print(f"{out}: {'ERRORS ' + str(errors[:3]) if errors else 'ok'}"
              f" pages={pages.group(1) if pages else '?'} overfull={overfull}"
              f" missing={sorted(set(missing))}")


if __name__ == "__main__":
    main(sys.argv[1:])
