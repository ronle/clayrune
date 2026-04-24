// Shared data + copy helpers for the Friendly Mission Control kit.

window.FC_ASSISTANTS = [
  { id: 'planner',  emoji: '🎂', name: 'Party Planner',      project: 'Birthday party planner',
    status: 'asking', dot: 'amber',
    summary: { casual: 'I need a couple of quick answers before I book the venue.',
               pro:    'Awaiting input: venue selection requires 2 decisions.' },
    currentTask: 'Drafting guest list email',
    lastAction: '2 minutes ago',
    steps: 3, steps_done: 2,
    cta: { casual: 'Answer questions', pro: 'Respond' } },

  { id: 'market', emoji: '📈', name: 'Market Watcher', project: 'Daily market digest',
    status: 'working', dot: 'green',
    summary: { casual: "I'm reading today's earnings reports. Almost done.",
               pro:    'Parsing Q1 earnings releases. ETA 3 min.' },
    currentTask: 'Reading earnings reports · 7 of 11',
    lastAction: 'just now',
    steps: 11, steps_done: 7,
    cta: { casual: 'Peek at progress', pro: 'View progress' } },

  { id: 'intern', emoji: '📝', name: 'Research Intern', project: 'Competitor research',
    status: 'done', dot: 'green',
    summary: { casual: "All done! I put a 2-page summary on your desk.",
               pro:    'Completed. Summary (2 pages) available for review.' },
    currentTask: 'Finished: "Top 5 competitor features"',
    lastAction: '8 minutes ago',
    steps: 5, steps_done: 5,
    cta: { casual: 'Read the summary', pro: 'Review output' } },

  { id: 'cam', emoji: '📸', name: 'Camera Helper', project: 'Youth sports footage',
    status: 'stuck', dot: 'red',
    summary: { casual: 'I got kicked out — can you help me sign back in?',
               pro:    'Authentication expired. User action required.' },
    currentTask: 'Needs: sign in to camera again',
    lastAction: '1 hour ago',
    steps: 4, steps_done: 2,
    cta: { casual: 'Help fix it', pro: 'Resolve issue' } },

  { id: 'writer', emoji: '✍️', name: 'Blog Writer', project: 'Weekly newsletter',
    status: 'idle', dot: 'grey',
    summary: { casual: "Ready when you are. What should this week's be about?",
               pro:    'Idle. Awaiting assignment.' },
    currentTask: 'Nothing scheduled',
    lastAction: '2 days ago',
    steps: 0, steps_done: 0,
    cta: { casual: 'Give me a topic', pro: 'Assign task' } },

  { id: 'scout', emoji: '🔎', name: 'Scout', project: 'Reddit mentions',
    status: 'working', dot: 'green',
    summary: { casual: "Watching 3 subreddits for mentions of your product.",
               pro:    'Monitoring 3 subreddits. Last check 2 min ago.' },
    currentTask: 'Checking r/productivity',
    lastAction: '2 minutes ago',
    steps: 1, steps_done: 0,
    cta: { casual: 'See what I found', pro: 'View findings' } },
];

window.FC_TODAY_GROUPS = [
  { id: 'asking',  titleCasual: 'Needs you',      titlePro: 'Awaiting input', ids: ['planner', 'cam'] },
  { id: 'working', titleCasual: 'Working on it',  titlePro: 'In progress',    ids: ['market', 'scout'] },
  { id: 'done',    titleCasual: 'Done today',     titlePro: 'Completed',      ids: ['intern'] },
  { id: 'idle',    titleCasual: 'Resting',        titlePro: 'Idle',           ids: ['writer'] },
];

window.FC_CHAT_SEED = [
  { from: 'you',   text: 'Can you put together next week\'s newsletter for me?' },
  { from: 'orch',  text: "Sure — I'll ask Blog Writer to start. Want it to cover anything specific, or should I pick the top 3 trends from this week?" },
  { from: 'you',   text: 'Top 3 trends is perfect. Keep it under 400 words.' },
  { from: 'orch',  text: "On it. I'll also loop in Scout to gather chatter from Reddit and X.",
    workcards: [
      { assistant: 'Blog Writer',  status: 'working', note: 'Drafting outline' },
      { assistant: 'Scout',        status: 'working', note: 'Gathering mentions' },
    ] },
  { from: 'orch',  text: "Quick question — should I include the news about the Fed? It\'s a big one but a bit political.", needsAnswer: true },
];

window.FC_SUGGESTIONS = [
  { emoji: '🎂', title: 'Plan a birthday party', sub: 'I\'ll handle venue, invites, and food' },
  { emoji: '📝', title: 'Research a topic',      sub: 'Give me a topic, I\'ll write a summary' },
  { emoji: '📬', title: 'Sort my inbox',         sub: 'I\'ll triage and draft replies' },
  { emoji: '🎨', title: 'Help me make something', sub: 'A newsletter, a pitch deck, a website…' },
];

window.FC_ONBOARDING = [
  { title: 'Meet your team',
    body: 'Mission Control gives you a team of assistants. Each one handles a project for you — planning a party, summarizing research, watching the news.',
    target: null },
  { title: 'This is a Project Card',
    body: 'Every project you\'re running shows up as a card. The card tells you what the assistant is doing right now — in plain English.',
    target: 'first-card' },
  { title: 'Tap the button to help out',
    body: 'When an assistant needs something from you, its card turns amber. Tap the button to answer its question.',
    target: 'cta' },
  { title: 'Start with a sample',
    body: 'We set up a "Birthday party planner" so you can try things without stakes. Explore, or dismiss it when you\'re ready.',
    target: null, last: true },
];
