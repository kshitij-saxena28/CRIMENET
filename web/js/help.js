// Plain-language help for every page: what it is for, how to use it, how to read the output, what to be careful about.
// Shown in the "How this page works" panel under each page title and used by the Guide dialog.
export const HELP = {
  command: {
    what: 'A one-screen summary of the case you have selected: how much is in the case, what the AI has flagged, and what needs a human decision.',
    steps: ['Pick a case in the top bar (admins can also view all cases).', 'Look at the banner: it counts alerts that still need a decision.', 'Open Signals & Evidence to work through the alerts, or Persons of Interest to see who to look at first.'],
    read: 'Numbers are counts of records in the case: entities are people, phones, vehicles, accounts, places and organisations; relationships are the recorded links between them.',
    caution: 'Alerts and scores rank things for a human to review. They are never a finding of guilt.',
  },
  ingest: {
    what: 'Turns a FIR, complaint or table into structured facts (people, phones, vehicles, accounts, dates, sections) and lets you check every fact before it enters the case.',
    steps: ['Upload a FIR (PDF, image or text) or a spreadsheet. Hindi and other Indian languages are translated for you.', 'Open the document and read the extracted facts next to the original text. Each fact shows the words it came from.', 'Accept, edit or reject each fact. Only accepted facts are used elsewhere.'],
    read: 'Confidence is how sure the software is about that one fact. Low confidence or a red flag means "look at this first".',
    caution: 'Scanned photos can be misread. Always compare important numbers (phones, accounts) with the original document.',
  },
  cases: {
    what: 'Your case workbenches: create a case, add tasks and notes, and see who is working on what.',
    steps: ['Create a workbench for a new case number.', 'Add tasks with an owner and due date.', 'Write notes as you go; they are saved with your name and time.'],
    read: 'Every change is recorded in the audit log with who did it and when.',
  },
  suspects: {
    what: 'A ranked shortlist of people and organisations in the case that deserve attention first, with the reasons and evidence behind each rank.',
    steps: ['Choose a case. The list is ranked from highest to lowest priority.', 'Open a person to see why they are ranked (shared phones, money flows, repeated appearance across FIRs, links to other accused).', 'Record your decision: confirm as person of interest, dismiss, or ask for more information. Give a reason.'],
    read: 'The score (0–100) is a lead priority, not a probability of guilt. The band shows how much data it is based on: a wide band means "little data, treat with care".',
    caution: 'Victims and complainants are excluded by default. A high rank means "worth checking", never "guilty". Your decision is recorded and changes the ranking.',
  },
  graph: {
    what: 'A map of how people, phones, vehicles, accounts and places are connected in the case.',
    steps: ['Neighbourhood: type an entity ID (like P001) to see everything around it.', 'Paths: find how two entities are connected.', 'Communities: see groups that are tightly linked; big circles are the most connected.', 'Entity resolution: find records that may be the same person and merge them (needs permission).'],
    read: 'Colours show the type of entity. A line is a recorded link with its source. Click a circle to see its details.',
    caution: 'A connection means two things appear together in the records. It does not prove wrongdoing.',
  },
  netai: {
    what: 'AI that suggests links that are probably missing, explains why, finds key connectors, and and finds key connectors.',
    steps: ['Link prediction: see suggested missing links with reasons; send good ones to review.', 'Explain: ask why two entities may be related.', 'Roles: find bridges and hubs and the people whose removal would split the network.'],
    read: 'Scores are the model\'s confidence that a link exists. The evaluation numbers at the top show how well the model does on the case itself.',
    caution: 'Suggestions are hypotheses to check. Nothing is added to the case automatically.',
  },
  timeline: {
    what: 'When and where things happened: a timeline of events, a map, and a FIR comparison.',
    steps: ['Timeline: filter by category, FIR or text; click an event for details.', 'Map: see event locations; it works offline.', 'Compare FIRs: put two FIRs side by side.'],
    read: 'Times are shown in your local time. Events without a place name or coordinates cannot be shown on the map.',
  },
  social: {
    what: 'Lawful social-media intelligence. You import posts that were lawfully obtained (an export, a court-ordered production, a public post you saved), or let the server collect them for you through official APIs and public feeds. The software reads them, flags risk language in English, Hindi and Hinglish, finds accounts that act in a coordinated way, and suggests links to people already in the case.',
    steps: ['Bring posts in: upload a JSON or CSV file, paste a post, or open Monitor to follow one account automatically (once or on a schedule).', 'Open Priority queue to see where to start reading; each line shows the points a rule added.', 'Open Analysis for coordinated posting, look-alike accounts, how a phrase spread, activity by hour and a places map.', 'Add watch-list terms, read posts in English, keep notes, and print a profile sheet for any account.', 'Open Links to see accounts that may match a person, phone or handle already in the case, then send good ones to review.'],
    read: 'A flag says which phrase matched and why it matters. A priority score is only a reading order: its reasons are listed line by line. A coordination group lists the accounts, the shared text and the time window.',
    caution: 'Nothing here scrapes the internet or logs in to any platform; collection uses official APIs and public feeds with keys your administrator sets. A flag is a lead for a human to read in context; sarcasm, quotes and news are common false alarms.',
  },
  surveillance: {
    what: 'A record of each authorised surveillance operation (who ordered it, the legal basis, the period) with a tamper-evident log of what was done and seen, and reports you can export for supervisors or court.',
    steps: ['Create an operation with its authority, legal basis and dates.', 'Add log entries as things happen: observation, contact, deployment, seizure.', 'Supervisors approve or close the operation.', 'Generate a report (PDF, Word, HTML or JSON).'],
    read: 'Every entry is chained to the one before it, so the report shows whether the log was edited afterwards.',
    caution: 'Only record operations that have proper written authority. Reports carry a watermark with who exported them.',
  },
  integrity: {
    what: 'Proof that records and evidence have not been altered. Every important action is written to a signed, hash-chained ledger. This page checks it and exports proof for court.',
    steps: ['Press Verify to check the whole ledger and the case data against it.', 'Read the verdict: Intact, Partial, or Tampering detected with the exact block.', 'Export a bundle to check offline with the verifier script.', 'Record the current head hash somewhere safe (a printout works).'],
    read: 'Each block holds the fingerprint of the one before it and is signed by the server. Change anything and every later link breaks.',
    caution: 'The ledger proves records were not changed. Removing the very latest blocks can only be proven if you kept an earlier head hash.',
  },
  copilot: {
    what: 'Ask questions about the case in plain language, for example "Who shares a phone with Rahul?".',
    steps: ['Select a case.', 'Type a question or click a suggestion.', 'Open the sources under the answer to check them.'],
    read: 'Every answer lists the records it is based on. If it cannot find support it says so instead of guessing.',
    caution: 'It never decides guilt. Check the cited records.',
  },
  signals: {
    what: 'Alerts raised by the AI, links between different FIRs, and the evidence vault with tamper-proof hashes.',
    steps: ['Alerts: open one to see why it was raised, then mark it reviewed or dismiss it.', 'FIR board: see FIRs that share phones, vehicles or accounts.', 'Evidence vault: upload evidence (supervisors) and verify that a file has not changed.'],
    read: 'A hash is a fingerprint of the file. If the fingerprint changes, the file changed.',
  },
  workflow: {
    what: 'Approvals (someone else must sign off), deadlines (for example charge-sheet dates), watchlists and notifications.',
    steps: ['Create an approval request; a different person approves or rejects it.', 'Add deadlines and export them to your calendar.', 'Add a number or name to a watchlist to be notified when it appears in new documents.'],
    read: 'You can never approve your own request. This is deliberate.',
  },
  governance: {
    what: 'Management view: how many cases are open, how long reviews take, data protection tools, backups and court-ready documents.',
    steps: ['Dashboard: see workload and backlog.', 'Legal packs: create a Section 63 BSA certificate and evidence index for court.', 'Backup: make an encrypted backup and verify it.', 'Access & sessions: see who logged in and force a sign-out if needed.'],
    read: 'Everything here is computed from real records, not estimates.',
  },
  reports: {
    what: 'Download case reports (JSON, CSV, Word, PDF) and check that the audit log has not been tampered with.',
    steps: ['Choose a case and a format.', 'Use "Verify audit" to confirm the log chain is intact.'],
    read: 'The audit log records who did what and when. It is chained so any edit is detected.',
  },
  requests: {
    what: 'People cannot sign themselves up. They send an access request. A supervisor approves it first, then an administrator creates the account.',
    steps: ['Supervisors: read the request and approve or reject it with a note.', 'Administrators: create the user only after supervisor approval, choosing the final role.'],
    read: 'Status shows where the request is: waiting for supervisor, approved, created or rejected.',
  },
  admin: {
    what: 'Administrator tools: users and roles, case access, and the demo data switch.',
    steps: ['Users: create users, change roles, set passwords, deactivate accounts.', 'Case access: choose who can see which case.', 'Demo data: switch the built-in practice data on or off. Real data is never touched.'],
    read: 'Changing a password or role signs that user out immediately.',
  },
};

export const GLOSSARY = [
  ['FIR', 'First Information Report: the police record of a complaint.'],
  ['Entity', 'A person, phone number, vehicle, bank account, place or organisation found in the records.'],
  ['Relationship', 'A recorded link between two entities, for example "owns" or "called".'],
  ['Community', 'A group of entities that are much more connected to each other than to the rest.'],
  ['Person of interest score', 'A 0–100 priority for who to look at first. It is not a probability of guilt.'],
  ['Confidence', 'How sure the software is about one extracted fact or one finding.'],
  ['MFA', 'Two-step sign-in: your password plus a 6-digit code from an authenticator app.'],
  ['Ledger', 'A signed, hash-chained log. Editing any past entry breaks the chain and is detected.'],
  ['Hash', 'A fingerprint of a file. If the file changes, the fingerprint changes.'],
  ['Maker-checker', 'One person proposes, a different person approves.'],
  ['Section 63 BSA certificate', 'The certificate that accompanies electronic evidence in court.'],
];
