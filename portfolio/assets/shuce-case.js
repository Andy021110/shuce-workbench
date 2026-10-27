'use strict';
(() => {
 const buttons=[...document.querySelectorAll('[data-replay]')];
 const panels=[...document.querySelectorAll('.replay-panels>section')];
 buttons.forEach(button=>button.addEventListener('click',()=>{
  const id='panel-'+button.dataset.replay;
  buttons.forEach(item=>item.setAttribute('aria-pressed',String(item===button)));
  panels.forEach(panel=>{panel.hidden=panel.id!==id;});
  document.getElementById('replay-status').textContent='正在查看'+button.textContent;
 }));
})();
