import { ActionLink } from '../ui/ActionLink';
import { isWorkspaceSample } from './workspaceFixture';
import cases from './moduleCases.json';
import { useLocation } from 'react-router';

export function RelatedCheckLink({ kind }: { kind: string }) {
  const location = useLocation();
  if (!isWorkspaceSample() || !['statistical_forensics', 'originality_reuse'].includes(kind)) return null;
  const statistics = kind === 'statistical_forensics';
  const document = cases.documents.find(item => item.id === (statistics ? cases.statistics.document_id : cases.reuse.checked_document_id))!;
  return <section className="signal-attached-flow" aria-label="Related manuscript analysis">
    <p>Analysis of another manuscript: <strong>{document.title}</strong></p>
    <ActionLink to={`/module-checks/${statistics ? 'statistics' : 'reuse'}`} state={{ returnTo: location.pathname + location.search + location.hash }} variant="secondary">{statistics ? 'Open statistical analysis' : 'Open passage comparison'}</ActionLink>
  </section>;
}
