'use strict';
(() => {
 const root=document.documentElement,appearance=document.getElementById('appearance'),reduce=document.getElementById('reduce-motion'),osMotion=matchMedia('(prefers-reduced-motion: reduce)');
 const read=key=>{try{return localStorage.getItem(key);}catch{return null;}};
 const save=(key,value)=>{try{localStorage.setItem(key,value);}catch{}};
 appearance.value=['light','dark'].includes(read('portfolio-appearance'))?read('portfolio-appearance'):'system';reduce.checked=read('portfolio-reduce-motion')==='true';
 function apply(){if(appearance.value==='system')delete root.dataset.theme;else root.dataset.theme=appearance.value;root.dataset.motion=reduce.checked||osMotion.matches?'reduce':'full';document.dispatchEvent(new Event('portfolio:motion-change'));}
 appearance.addEventListener('change',()=>{save('portfolio-appearance',appearance.value);apply();});reduce.addEventListener('change',()=>{save('portfolio-reduce-motion',String(reduce.checked));apply();});osMotion.addEventListener('change',apply);apply();
 const viewer=document.getElementById('image-viewer'),image=document.getElementById('viewer-image');let opener=null;
 document.querySelectorAll('.image-preview').forEach(link=>link.addEventListener('click',event=>{
  if(event.button!==0||event.metaKey||event.ctrlKey||event.altKey||event.shiftKey||!viewer.showModal)return;
  event.preventDefault();opener=link;const source=link.querySelector('img');document.getElementById('viewer-title').textContent=source.alt;document.getElementById('viewer-caption').textContent=link.closest('figure')?.querySelector('figcaption')?.textContent||'';image.alt=source.alt;image.src=link.href;viewer.showModal();document.body.style.overflow='hidden';document.getElementById('viewer-close').focus();
 }));
 document.getElementById('viewer-close').addEventListener('click',()=>viewer.close());
 viewer.addEventListener('click',event=>{const box=viewer.getBoundingClientRect();if(event.target===viewer&&(event.clientX<box.left||event.clientX>box.right||event.clientY<box.top||event.clientY>box.bottom))viewer.close();});
 viewer.addEventListener('close',()=>{document.body.style.overflow='';opener?.focus({preventScroll:true});});
})();
