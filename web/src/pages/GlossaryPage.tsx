import { Fragment } from "react";
import { PageHead, Panel } from "../components/ui";
import { GLOSSARY, GLOSSARY_SECTIONS } from "../lib/glossary";

export default function GlossaryPage() {
  return (
    <>
      <PageHead title="Glossary"
        desc="What the numbers mean, in plain English. Anywhere in Courtside, a label with a dotted underline shows its definition when you point at it." />
      <div className="grid cols-2" style={{ alignItems: "start" }}>
        {GLOSSARY_SECTIONS.map((s) => (
          <Panel key={s.title} title={s.title}>
            <dl className="glossary">
              {s.keys.map((k) => (
                <Fragment key={k}>
                  <dt>{GLOSSARY[k].term}</dt>
                  <dd>{GLOSSARY[k].def}</dd>
                </Fragment>
              ))}
            </dl>
          </Panel>
        ))}
      </div>
    </>
  );
}
