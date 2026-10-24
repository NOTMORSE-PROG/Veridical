import { EARLIER_RULE_FACTS_HEADING, RULE_FACTS_HEADING, type RuleFactsPresentation } from "../domain/criterionRuleFacts";

export function RuleFactsDetails({ facts, earlier = false }: { facts: RuleFactsPresentation; earlier?: boolean }) {
  return <div className="signal-copy-flow signal-rule-facts">
    <h4>{earlier ? EARLIER_RULE_FACTS_HEADING : RULE_FACTS_HEADING}</h4>
    {facts.paragraphs.map((paragraph, index) => <div className="signal-attached-flow" key={paragraph}>
      <p>{paragraph}</p>
      {index === 0 && facts.headings.length > 0 && <ol>{facts.headings.map((heading, order) => <li key={order}>{heading}</li>)}</ol>}
    </div>)}
  </div>;
}
