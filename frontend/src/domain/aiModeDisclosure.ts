export const TEST_AI_DISCLOSURE = {
  title: "Test-mode AI responses",
  body: "This run is recorded in test AI mode. AI responses use fixtures instead of real AI grading. Do not treat AI-derived judgments as a real assessment of the manuscript. Check the recorded evidence directly.",
} as const;

export const UNKNOWN_AI_DISCLOSURE = {
  title: "AI response mode could not be verified",
  body: "This record does not establish whether responses came from a real external model or simulated fixtures. Check the recorded evidence directly before relying on AI-derived results.",
  finding: "This record does not establish whether responses came from a real external model or simulated fixtures. Check the recorded evidence directly before acting on this finding.",
  registry: "This check’s AI response mode could not be verified. The mode notice does not establish whether its source records came from a fresh external lookup. Check the saved source evidence directly.",
} as const;
