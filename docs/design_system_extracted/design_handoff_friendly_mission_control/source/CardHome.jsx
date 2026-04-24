// Card Home: a warm dashboard of project cards.

function ProjectCard({ p, voice, onOpen, innerRef, ctaRef }) {
  const summary = p.summary[voice];
  const cta = p.cta[voice];
  const label = window.statusLabel(p.status, voice);
  const metaTime = (voice === 'pro' ? 'Last action: ' : '') + p.lastAction;
  const dotColor = ({ working: 'green', asking: 'amber', stuck: 'red', done: 'green', idle: 'grey' })[p.status];
  return (
    <div ref={innerRef} className={'fc-card fc-project-card status-' + p.status} onClick={onOpen}>
      <div className="fc-pc-head">
        <window.Avatar emoji={p.emoji} />
        <div className="fc-pc-title">
          <div className="fc-pc-name">{p.name}</div>
          <div className="fc-pc-project">{p.project}</div>
        </div>
        <div className="fc-spacer"></div>
        <window.FcChip status={p.status}>{label}</window.FcChip>
      </div>

      <div className="fc-pc-summary">{summary}</div>

      {p.steps > 0 && (
        <div className="fc-row">
          <window.Progress done={p.steps_done} total={p.steps} color={p.status === 'done' ? 'green' : ''} />
          <span className="fc-eyebrow-inline">{p.steps_done}/{p.steps}</span>
        </div>
      )}

      <div className="fc-pc-meta">
        <span>{p.currentTask}</span>
        <span>·</span>
        <span>{metaTime}</span>
      </div>

      <div className="fc-pc-foot">
        <button ref={ctaRef} className={'fc-btn ' + (p.status === 'asking' || p.status === 'stuck' ? 'primary' : 'ghost')}
                onClick={(e) => { e.stopPropagation(); onOpen(); }}>
          {cta}
        </button>
      </div>
    </div>
  );
}

function CardHomeLayout({ voice, onNew, refs }) {
  const ps = window.FC_ASSISTANTS;
  // Order: asking/stuck first, then working, then idle/done.
  const order = { asking: 0, stuck: 1, working: 2, idle: 3, done: 4 };
  const sorted = [...ps].sort((a, b) => (order[a.status] ?? 9) - (order[b.status] ?? 9));
  return (
    <div>
      <window.GreetingHeader voice={voice} variant="home" onNew={onNew} />
      <div className="fc-card-grid">
        {sorted.map((p, i) => (
          <ProjectCard
            key={p.id}
            p={p}
            voice={voice}
            onOpen={() => {}}
            innerRef={i === 0 ? refs['first-card'] : undefined}
            ctaRef={i === 0 ? refs['cta'] : undefined}
          />
        ))}
      </div>
      <div className="fc-advanced-hint">
        {voice === 'pro'
          ? <>Need developer tools? <a>Open Advanced</a></>
          : <>Looking for extra buttons and logs? <a>Show advanced options</a></>}
      </div>
    </div>
  );
}

window.CardHomeLayout = CardHomeLayout;
