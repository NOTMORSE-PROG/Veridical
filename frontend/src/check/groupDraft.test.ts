import { describe, expect, it } from "vitest";
import { draftWasSaved, type GroupDraft } from "./groupDraft";

const draft: GroupDraft = { group_name: "Real Team", member_names: ["Ana Reyes", "Maria Santos"], program_id: 1, refused: false };

describe("whether a read-back record shows the typed group was saved", () => {
  it("matches only when the name, members and program all match", () => {
    expect(draftWasSaved(draft, { name: " real  team ", member_names: ["maria santos", "Ana Reyes"], program_id: 1 })).toBe(true);
  });

  it("keeps the draft when only the name matches, the usual edit of an existing group", () => {
    expect(draftWasSaved(draft, { name: "Real Team", member_names: [], program_id: 1 })).toBe(false);
    expect(draftWasSaved(draft, { name: "Real Team", member_names: ["Ana Reyes", "Maria Santos"], program_id: null })).toBe(false);
  });
});
