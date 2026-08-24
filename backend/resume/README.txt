Easiest: open Settings -> Base Resume (and, optionally, Experience Bank) in
the running app and paste text there. It writes the files below right here
for you — no need to touch this directory by hand.

If you'd rather drop the file yourself (e.g. for a scripted deployment):

  base_resume.txt       — ATS-plain-text version (required; used directly in LLM scoring/tailoring prompts)
  experience_bank.txt   — optional; older roles/projects that don't fit the base resume's one-page layout but
                           are fair game for tailoring to pull in per-job. Plain text, same format as
                           base_resume.txt. Leave unset to tailor from the base resume alone.

There is no base_resume.pdf. Every tailored resume version is rendered by
reportlab in a fixed style (navy Times-Roman headers, 35pt margins — see
app/resume_tailor.py) baked into the code; the app never reads a PDF from
this folder, so there's nothing to supply here beyond the plain-text file(s)
above.

None of these files are checked in — this directory is a mount point for your own resume files.
