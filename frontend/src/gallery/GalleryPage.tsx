// Component gallery — internal review route for V-002. Renders every base
// primitive (DESIGN.md §2) with all variants for side-by-side comparison
// against design/wireframes.html. Not a product screen (no wireframe ID).
import type { ReactNode } from "react";
import { ApiStatus } from "../components/ApiStatus";
import { Button } from "../components/Button";
import { KpiCard } from "../components/KpiCard";
import { Modal, ModalBackdrop } from "../components/Modal";
import { Panel } from "../components/Panel";
import { Pill } from "../components/Pill";
import { Stepper } from "../components/Stepper";
import { SeverityTag } from "../components/SeverityTag";

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3">
      <h2 className="text-2xs font-semibold tracking-header uppercase text-table-head">
        {title}
      </h2>
      {children}
    </section>
  );
}

export function GalleryPage() {
  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-8 p-8">
      <header>
        <h1 className="text-lg font-bold">Component gallery</h1>
        <p className="text-xs text-ink-faint">
          Base primitives per DESIGN.md §2 — compare against
          design/wireframes.html
        </p>
      </header>

      <Section title="API smoke check (V-048)">
        <ApiStatus />
      </Section>

      <Section title="Buttons">
        <div className="flex flex-wrap items-center gap-2">
          <Button>Default</Button>
          <Button variant="primary">Primary</Button>
          <Button size="sm">Small</Button>
          <Button variant="primary" size="sm">
            Small primary
          </Button>
          <Button disabled>Disabled</Button>
          <Button variant="primary" disabled>
            Disabled primary
          </Button>
        </div>
      </Section>

      <Section title="Status pills">
        <div className="flex flex-wrap items-center gap-2">
          <Pill status="ok">Ready</Pill>
          <Pill status="warn">Conditionally ready</Pill>
          <Pill status="bad">Not ready</Pill>
          <Pill status="processing">Processing</Pill>
          <Pill status="queued">Queued</Pill>
        </div>
      </Section>

      <Section title="Severity tags">
        <div className="flex flex-wrap items-center gap-2">
          <SeverityTag severity="high" />
          <SeverityTag severity="med" />
          <SeverityTag severity="low" />
        </div>
      </Section>

      <Section title="Panel with header row">
        <Panel header="Manuscripts">
          <div className="grid grid-cols-[1fr_auto_auto] items-center gap-2.5 border-t border-border-soft px-3.5 py-2">
            <span>Group 3, Smart irrigation system</span>
            <SeverityTag severity="low" />
            <Pill status="ok">Ready</Pill>
          </div>
          <div className="grid grid-cols-[1fr_auto_auto] items-center gap-2.5 border-t border-border-soft px-3.5 py-2">
            <span>Group 7, Inventory forecasting</span>
            <SeverityTag severity="med" />
            <Pill status="warn">Conditionally ready</Pill>
          </div>
        </Panel>
      </Section>

      <Section title="KPI cards">
        <div className="flex flex-wrap gap-3">
          <KpiCard value="24" label="Criteria" />
          <KpiCard value="6" label="Manuscripts checked" />
          <KpiCard value="3" label="Escalated items" />
        </div>
      </Section>

      <Section title="Stepper">
        <Panel className="p-3.5">
          <Stepper
            steps={[
              {
                id: "extract",
                label: "Extract text & layout",
                state: "done",
                tagText: "Done",
                detail: "12 pages, 3 s",
              },
              {
                id: "decompose",
                label: "AI decomposition into criteria",
                state: "running",
                tagText: "In progress",
              },
              {
                id: "validate",
                label: "Validation gate",
                state: "pending",
                tagText: "Not started yet",
              },
              { id: "review", label: "Your review", state: "pending", tagText: "Not started yet" },
            ]}
          />
        </Panel>
      </Section>

      <Section title="Modal on dim backdrop">
        <ModalBackdrop>
          <Modal
            title="Upload required format"
            onClose={() => undefined}
            footer={
              <>
                <Button>Cancel</Button>
                <Button variant="primary" disabled>
                  Continue to review
                </Button>
              </>
            }
          >
            <p className="text-sm text-ink-soft">
              Upload the rubric or format document (PDF / DOCX). Nothing runs
              until you confirm.
            </p>
          </Modal>
        </ModalBackdrop>
      </Section>
    </main>
  );
}
