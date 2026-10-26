// Faithful plain-language versions of this published sample's prepared reading
// notes. Apply only when the saved explanation still matches that exact note.
// Teacher comments, private notes and other saved explanations are kept verbatim.
export const PLAIN_CRITERION_REASONS: Readonly<Record<number, string>> = {
  1: "Check whether the paper clearly separates completed work from future plans. The abstract (page 5) and code (pages 37-50) show a C console program that saves text files. The conclusion (page 109) claims database integration. Page 29 says there is no database, while pages 103-108 describe a database and web interface as future work. Choose the rubric grade after comparing these parts.",
  2: "Check whether the testing supports the paper's claims. Page 23 describes tests that are planned. Pages 32-33 rate performance Good or Excellent, but give no measured times, user count or test results. The code and screenshots show a working console prototype; they do not prove those ratings. Read the evidence before grading the content.",
  3: "Check the writing against the course standard. Page 3 has a subject-verb error and says 'top any other'. Page 4 switches between 'We', 'I' and 'our'. Page 22 spells developers as 'develpoers'. These are examples to review in context, not an automatic grade for the whole paper.",
  4: "Check the headings, section order and references between sections. The contents page lists the requirements, diagrams, methods, testing, limits, code and user manual. Diagrams and screenshots are present. The rubric on page 1 names four grade levels, but does not explain what earns each one. The teacher must apply the course standard; no default grade is assumed.",
};

export const PLAIN_FINDING_REASONS: Readonly<Record<number, string>> = {
  1: "The conclusion says a database was included. The limits section says there is no database. The code on pages 37-50 saves text files, and page 103 lists a database as future work. Ask the authors to make the conclusion match what they built. This is a mismatch in the description, not evidence of misconduct.",
  2: "Page 20 describes a web system. The abstract, C code and user-manual screenshots show a Windows console program. Pages 103-104 list a web interface as future work. Ask the authors to say whether page 20 describes the original plan and to separate that plan from the finished prototype.",
  3: "The summary reports 32 components: 29 complete and 3 partial. The table lists 23 rows on page 32 and 11 on page 33: 34 in total, with 31 complete and 3 partial. Headers and summary bullets are not counted. The printed 90.6% matches 29 out of 32. Ask which items the summary excludes, or ask the authors to correct the list and totals.",
  4: "Page 7 makes a claim about research and case studies without naming a study in that sentence. The reference section on page 109 thanks the instructor but does not list research sources. Ask for the sources behind the claim and their full reference entries. No source has been called fake, and no external database search was run for this observation.",
};
