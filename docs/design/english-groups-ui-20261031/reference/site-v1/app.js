'use strict';
const storageKey = 'kt-english-groups-demo-v1';
const questions = [
  {title:'Why did Maya want to use the empty land?', options:['To build a new library.','To create a shared garden.','To open a vegetable shop.','To make a children’s playground.'], answer:1, explanation:'第一段说明 Maya 希望邻居们能一起种菜、共度时光，因此她想建立一个共享花园。'},
  {title:'How did the group deal with the dry weather?', options:['They stopped growing vegetables.','They moved the plants into the library.','They collected rainwater and planned the watering.','They asked Mr Chen to work alone.'], answer:2, explanation:'第三段直接提到 collected rainwater 和 made a watering schedule，对应选项 C。'},
  {title:'What did Maya value most about the garden?', options:['The friendships it helped to build.','The money from selling vegetables.','The size of the final harvest.','The lessons from a gardening book.'], answer:0, explanation:'最后一段说明，Maya 认为建立的友谊比蔬菜更珍贵。'}
];
const blanks = [
  ['went','go 的过去式，与 Last Saturday 对应。'],['first','one 的序数词形式，表示第一次。'],['them','动词 welcomed 后使用人称代词宾格。'],['carefully','用副词修饰 cleaned。'],['smaller','than 提示使用 small 的比较级。'],['was','过去时，主语 One little dog 为单数。'],['sat','sit 的过去式。'],['minutes','a few 后使用可数名词复数。'],['slowly','用副词修饰 walked。'],['friendly','friend 的形容词形式，表示友好的。'],['helping','enjoy 后接动词的 -ing 形式。'],['animals','these 后使用可数名词复数。'],['happier','than 提示比较级，happy 变为 happier。'],['return','want to 后接动词原形。'],['kindness','a little 后需要名词，kind 的名词形式是 kindness。']
];
const paragraphs = [
  'Last Saturday, Ella {{1}} (go) to a small animal shelter with her brother. It was her {{2}} (one) visit, so she felt a little nervous. When they arrived, a worker welcomed {{3}} (they) and explained the plan for the day.',
  'First, they cleaned the rooms {{4}} (careful). The dogs were {{5}} (small) than she had expected. One little dog {{6}} (be) very quiet at first. Ella {{7}} (sit) beside it and waited for a few {{8}} (minute). The dog {{9}} (slow) walked towards her and touched her hand. Soon, it became much more {{10}} (friend).',
  '“I really enjoy {{11}} (help) here,” Ella told her brother. “These {{12}} (animal) need both care and company.” By the end of the day, she felt {{13}} (happy) than she had that morning. She wanted to {{14}} (return) the next weekend. Ella had learned that a little {{15}} (kind) could make a big difference.'
];
const emptyState = () => ({version:1,reading:[null,null,null],cloze:Array(15).fill(''),submitted:false,savedAt:null});
let state = emptyState(), view = 'reading', selectedQuestion = null, toastTimer, storageError = false;
const $ = id => document.getElementById(id);
function normalize(value){ return value.trim().toLowerCase().replace(/\s+/g,' '); }
function hasAnswer(group,index){ return group === 'reading' ? state.reading[index] !== null : Boolean(state.cloze[index].trim()); }
function count(group){ return state[group].filter((_,index)=>hasAnswer(group,index)).length; }
function total(){ return count('reading')+count('cloze'); }
function correct(group,index){ return hasAnswer(group,index) && (group === 'reading' ? state.reading[index] === questions[index].answer : normalize(state.cloze[index]) === blanks[index][0]); }
function escaped(value){ const el=document.createElement('span'); el.textContent=value; return el.innerHTML.replace(/"/g,'&quot;'); }
try {
  const saved=JSON.parse(localStorage.getItem(storageKey)||'null');
  if(saved?.version===1 && Array.isArray(saved.reading) && saved.reading.length===3 && saved.reading.every(v=>v===null || Number.isInteger(v) && v>=0 && v<4) && Array.isArray(saved.cloze) && saved.cloze.length===15 && saved.cloze.every(v=>typeof v==='string')){
    state={...emptyState(),...saved,submitted:saved.submitted===true};
  }
} catch {storageError=true;}
function persist(){
  state.savedAt=new Date().toISOString();
  try{ localStorage.setItem(storageKey,JSON.stringify(state)); storageError=false; }catch{ storageError=true; }
  renderSave();
}
function renderSave(){
  document.querySelector('.save-card').classList.toggle('error',storageError);
  $('save-label').textContent=storageError?'本机保存失败':state.submitted?'本次练习已提交':state.savedAt?'本机草稿已保存':'本机草稿已就绪';
  $('save-detail').textContent=storageError?'答案暂存于页面，请勿关闭或刷新':state.savedAt?'保存在此浏览器 · '+new Date(state.savedAt).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):'选择或输入后自动保存';
}
function renderReading(){
  $('reading-questions').innerHTML=questions.map((q,i)=>`<section class="question" id="question-${i}" aria-labelledby="q-title-${i}"><div class="question-heading"><span class="question-number">${i+1}</span><h4 id="q-title-${i}" lang="en">${q.title}</h4></div><div class="q-meta"><span>单选题 · 2 分</span><button class="text-button clear-choice" data-clear="${i}" ${state.submitted || state.reading[i]===null?'disabled':''}>清除选择</button></div><div role="radiogroup" aria-labelledby="q-title-${i}">${q.options.map((option,j)=>`<label class="option ${state.submitted&&j===q.answer?'correct-option':''} ${state.submitted&&state.reading[i]===j&&j!==q.answer?'incorrect-option':''}"><input type="radio" name="reading-${i}" value="${j}" data-reading="${i}" ${state.reading[i]===j?'checked':''} ${state.submitted?'disabled':''}><span class="option-key">${'ABCD'[j]}</span><span lang="en">${option}</span></label>`).join('')}</div>${state.submitted?`<div class="feedback ${!hasAnswer('reading',i)?'unanswered':correct('reading',i)?'':'wrong'}"><strong>${!hasAnswer('reading',i)?'未作答':correct('reading',i)?'回答正确 · 2 / 2 分':'回答错误 · 0 / 2 分'} · 正确答案 ${'ABCD'[q.answer]}</strong>${q.explanation}</div>`:''}</section>`).join('');
}
function renderCloze(){
  $('cloze-passage').innerHTML=paragraphs.map(text=>`<p>${text.replace(/\{\{(\d+)\}\}/g,(_,n)=>{const i=Number(n)-1;return `<span class="blank-wrap" id="blank-wrap-${i}"><label class="blank-label" for="blank-${i}">${n}</label><input class="blank-input ${state.cloze[i].trim()?'filled':''} ${state.submitted?(correct('cloze',i)?'correct':'wrong'):''}" id="blank-${i}" data-blank="${i}" aria-label="第 ${n} 空" type="text" autocomplete="off" autocapitalize="off" spellcheck="false" maxlength="40" value="${escaped(state.cloze[i])}" ${state.submitted?'disabled':''}>${state.submitted?`<span class="status-mark" aria-hidden="true">${!hasAnswer('cloze',i)?'—':correct('cloze',i)?'✓':'×'}</span>`:''}</span>`})}</p>`).join('');
  $('cloze-feedback').hidden=!state.submitted;
  if(state.submitted){$('cloze-feedback').innerHTML='<h3>逐空答案与解析</h3>'+blanks.map((b,i)=>`<div class="feedback-row"><span>${String(i+1).padStart(2,'0')}</span><span class="feedback-answer ${correct('cloze',i)?'':'answer-wrong'}">${b[0]}</span><span>${!hasAnswer('cloze',i)?'未作答。':!correct('cloze',i)?'你的答案：'+escaped(state.cloze[i])+'。':''}${b[1]}</span></div>`).join('');}
}
function createMaps(){
  for(const group of ['reading','cloze']){
    $(group+'-map').innerHTML=state[group].map((_,i)=>`<button class="map-number" data-jump="${group}" data-index="${i}">${i+1}</button>`).join('');
  }
}
function updateProgress(){
  const n=total();
  for(const group of ['reading','cloze']){
    const size=state[group].length, done=count(group);
    $(group+'-tab-count').textContent=`${done} / ${size}`;
    $(group+'-count').textContent=`${done}/${size}`;
    document.querySelectorAll(`[data-jump="${group}"]`).forEach((button,i)=>{
      const answered=hasAnswer(group,i), status=state.submitted?(answered?(correct(group,i)?'正确':'错误'):'未作答'):(answered?'已作答':'未作答');
      button.className='map-number'+(answered?' answered':'')+(selectedQuestion===group+'-'+i?' current':'')+(state.submitted?(answered?(correct(group,i)?' is-correct':' is-wrong'):' is-unanswered'):'');
      button.setAttribute('aria-label',`${group==='reading'?'阅读理解第':'短文填空第'} ${i+1} ${group==='reading'?'题':'空'}，${status}`);
    });
  }
  $('total-counter').innerHTML=`${n} <span>/ 18</span>`;
  $('progress-fill').style.width=(n/18*100)+'%';
  document.querySelector('[role="progressbar"]').setAttribute('aria-valuenow',String(n));
  $('progress-copy').textContent=state.submitted?'练习已提交 · 可查看逐题反馈':n===18?'全部完成，可以检查并提交':`还有 ${18-n} 个小题待完成`;
  $('group-progress').textContent=`本组已作答 ${count(view)} / ${state[view].length}`;
  $('jump-missing').hidden=state.submitted || n===18;
  $('submit').textContent=state.submitted?'已提交 · 答案已锁定':'提交练习 →';
  $('submit').disabled=state.submitted;
  $('submit-hint').textContent=state.submitted?'切换题组查看反馈；重新体验可再次作答。':'提交后锁定答案，并展示本次练习反馈。';
  $('lock-legend').hidden=!state.submitted;
  $('result-card').hidden=!state.submitted;
  if(state.submitted){
    const readingCorrect=questions.filter((_,i)=>correct('reading',i)).length,clozeCorrect=blanks.filter((_,i)=>correct('cloze',i)).length;
    $('result-card').innerHTML=`<h2>本次练习结果</h2><div class="result-score">${readingCorrect*2+clozeCorrect}<span> / 21 分</span></div><p>阅读理解：${readingCorrect*2} / 6 分</p><p>短文填空：${clozeCorrect} / 15 分</p><p>正确 ${readingCorrect+clozeCorrect} · 错误 ${n-readingCorrect-clozeCorrect} · 未答 ${18-n}</p>`;
  }
}
function setView(next,focus=false){
  view=next==='cloze'?'cloze':'reading';
  $('reading-view').hidden=view!=='reading';$('cloze-view').hidden=view!=='cloze';
  document.querySelectorAll('.exercise-tab').forEach(tab=>{tab.classList.toggle('active',tab.dataset.view===view); if(tab.dataset.view===view)tab.setAttribute('aria-current','page');else tab.removeAttribute('aria-current');});
  $('section-kicker').textContent=view==='reading'?'PART 01 · READING':'PART 02 · GAP FILLING';
  $('exercise-title').textContent=view==='reading'?'阅读理解':'短文填空';
  $('points').textContent=view==='reading'?'3 小题 · 每题 2 分':'15 个空 · 每空 1 分';
  $('instructions').textContent=view==='reading'?'阅读短文，根据文章内容，为每个问题选择一个最佳答案。':'根据上下文，填入括号内单词的适当形式。每空一个单词，不区分大小写。';
  $('next-group').innerHTML=view==='reading'?'下一组：短文填空 <span aria-hidden="true">→</span>':'<span aria-hidden="true">←</span> 返回阅读理解';
  try{history.replaceState(null,'','#'+view);}catch{}
  updateProgress();
  if(focus)$('exercise-title').focus({preventScroll:true});
}
function jump(group,index){
  setView(group);selectedQuestion=group+'-'+index;updateProgress();
  const element=group==='reading'?$('question-'+index):$('blank-'+index);
  element.scrollIntoView({behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'instant':'smooth',block:'center'});
  if(group==='cloze'&&!state.submitted)element.focus({preventScroll:true});
  if(group==='reading'){element.classList.remove('flash');void element.offsetWidth;element.classList.add('flash');const target=element.querySelector('input:checked')||element.querySelector('input');if(!state.submitted)target.focus({preventScroll:true});}
}
function toast(message){$('toast').textContent=message;$('toast').hidden=false;clearTimeout(toastTimer);toastTimer=setTimeout(()=>$('toast').hidden=true,3500);}
document.addEventListener('click',event=>{
  const viewButton=event.target.closest('[data-view]');if(viewButton){setView(viewButton.dataset.view,true);return;}
  const jumpButton=event.target.closest('[data-jump]');if(jumpButton){jump(jumpButton.dataset.jump,Number(jumpButton.dataset.index));return;}
  const clear=event.target.closest('[data-clear]');if(clear&&!state.submitted){const i=Number(clear.dataset.clear);state.reading[i]=null;document.querySelectorAll(`input[name="reading-${i}"]`).forEach(input=>input.checked=false);clear.disabled=true;persist();updateProgress();}
});
document.addEventListener('change',event=>{const input=event.target;if(input.matches('[data-reading]')&&!state.submitted){const i=Number(input.dataset.reading);state.reading[i]=Number(input.value);selectedQuestion='reading-'+i;document.querySelector(`[data-clear="${i}"]`).disabled=false;persist();updateProgress();}});
document.addEventListener('input',event=>{const input=event.target;if(input.matches('[data-blank]')&&!state.submitted){state.cloze[Number(input.dataset.blank)]=input.value;input.classList.toggle('filled',Boolean(input.value.trim()));persist();updateProgress();}});
document.addEventListener('focusin',event=>{const input=event.target;if(input.matches('[data-blank]')){selectedQuestion='cloze-'+input.dataset.blank;updateProgress();}else if(input.matches('[data-reading]')){selectedQuestion='reading-'+input.dataset.reading;updateProgress();}});
$('next-group').addEventListener('click',()=>{setView(view==='reading'?'cloze':'reading',true);$('exercise-title').scrollIntoView({block:'start',behavior:'smooth'});});
$('jump-missing').addEventListener('click',()=>{const order=[view,view==='reading'?'cloze':'reading'];for(const group of order){const index=state[group].findIndex((_,i)=>!hasAnswer(group,i));if(index!==-1){jump(group,index);return;}}});
$('text-size').addEventListener('click',()=>{const large=document.querySelector('.reading-material .passage').classList.toggle('large');$('text-size').setAttribute('aria-pressed',String(large));$('text-size').setAttribute('aria-label',large?'恢复文章字号':'放大文章字号');});
$('submit').addEventListener('click',()=>{if(state.submitted)return;const missing=18-total();$('dialog-summary').textContent=missing?`你已完成 ${total()} / 18 个小题，还有 ${missing} 个小题未作答。未作答项会单独标注，本次练习按 0 分计。`:'18 个小题均已作答。检查完成后，就可以提交了。';$('submit-dialog').showModal();});
$('submit-dialog').addEventListener('close',()=>{if($('submit-dialog').returnValue!=='submit'||state.submitted)return;state.submitted=true;persist();renderReading();renderCloze();updateProgress();toast('练习已提交，可以查看每小题的反馈。');});
$('reset').addEventListener('click',()=>$('reset-dialog').showModal());
$('reset-dialog').addEventListener('close',()=>{if($('reset-dialog').returnValue!=='reset')return;state=emptyState();selectedQuestion=null;persist();renderReading();renderCloze();setView('reading',true);toast('已开始新的体验。');});
window.addEventListener('hashchange',()=>setView(location.hash==='#cloze'?'cloze':'reading'));
renderReading();renderCloze();createMaps();setView(location.hash==='#cloze'?'cloze':'reading');renderSave();
