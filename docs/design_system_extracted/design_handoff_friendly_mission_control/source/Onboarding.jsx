// Onboarding tour overlay.

function OnboardingTour({ onClose, refs }) {
  const [i, setI] = React.useState(0);
  const [rect, setRect] = React.useState(null);
  const step = window.FC_ONBOARDING[i];

  React.useEffect(() => {
    if (!step.target) { setRect(null); return; }
    const el = refs[step.target]?.current;
    if (!el) { setRect(null); return; }
    const r = el.getBoundingClientRect();
    setRect({ top: r.top - 8, left: r.left - 8, width: r.width + 16, height: r.height + 16 });
  }, [i, step.target]);

  const next = () => {
    if (i >= window.FC_ONBOARDING.length - 1) onClose();
    else setI(i + 1);
  };

  return (
    <div className="fc-tour-backdrop" onClick={onClose}>
      {rect && <div className="fc-tour-spotlight" style={rect}></div>}
      <div className="fc-tour-card" onClick={(e) => e.stopPropagation()}>
        <h3>{step.title}</h3>
        <p>{step.body}</p>
        <div className="fc-tour-dots">
          {window.FC_ONBOARDING.map((_, j) => (
            <span key={j} className={'d' + (j <= i ? ' active' : '')}></span>
          ))}
        </div>
        <div className="fc-tour-actions">
          <button className="fc-tour-skip" onClick={onClose}>Skip tour</button>
          <button className="fc-btn primary small" onClick={next}>
            {step.last ? 'Got it' : 'Next'}
          </button>
        </div>
      </div>
    </div>
  );
}

function TweaksPanel({ state, setState }) {
  const btn = (key, val, label) => (
    <button className={state[key] === val ? 'active' : ''} onClick={() => setState(s => ({ ...s, [key]: val }))}>
      {label}
    </button>
  );
  return (
    <div className="fc-tweaks">
      <h4>Design tweaks</h4>
      <div className="fc-tweak-group">
        <div className="fc-tweak-label">Layout preview</div>
        <div className="fc-tweak-row">
          {btn('layout', 'home',  'Cards')}
          {btn('layout', 'chat',  'Chat')}
          {btn('layout', 'today', 'Today list')}
        </div>
      </div>
      <div style={{ fontSize: 12, color: 'var(--ink-dim)', lineHeight: 1.5, marginTop: 8 }}>
        Theme, accent, density, and voice now live in <strong>Settings</strong> in the sidebar.
      </div>
    </div>
  );
}

window.OnboardingTour = OnboardingTour;
window.TweaksPanel = TweaksPanel;
