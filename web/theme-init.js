try { document.documentElement.dataset.theme = localStorage.getItem('dcn-theme') || 'light'; } catch (e) { document.documentElement.dataset.theme = 'light'; }
