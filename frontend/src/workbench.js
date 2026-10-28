
let current = null;
let cutoutCandidate=null, cutoutOriginalUrl=null, cutoutResultUrl=null, cutoutSourceFile=null;
function resetCutoutPreview(){
  cutoutCandidate=null;cutoutSourceFile=null;
  if(cutoutOriginalUrl)URL.revokeObjectURL(cutoutOriginalUrl);
  if(cutoutResultUrl)URL.revokeObjectURL(cutoutResultUrl);
  cutoutOriginalUrl=null;cutoutResultUrl=null;
  document.querySelector('[name="product_source_mode"]').value='original';
  $('cutoutCompare').hidden=true;$('acceptCutout').disabled=true;$('resetCutout').disabled=true;
  $('cutoutStatus').textContent='已保留原图；如需抠图，请重新预览。';
}
let activeReviewRunId = null;
let activeReviewApproved = false;
const $ = id => document.getElementById(id);
function reviewDraftSession(context,variant,reviewer,containerId,attribute,prefix){
  const container=$(containerId);
  const rows=()=>[...container.querySelectorAll('['+attribute+']')]
    .filter(row=>context.fields_to_review.includes(row.getAttribute(attribute)));
  return new window.ReviewDraftAutosave({
    context,container,
    url:'/api/posters/'+context.poster_id+'/versions/'+context.version+'/review-draft?'+
      new URLSearchParams({variant,reviewer}),
    status:$(prefix+'DraftStatus'),saveButton:$('save'+(prefix==='human'?'Human':'Native')+'Draft'),
    reloadButton:$('reload'+(prefix==='human'?'Human':'Native')+'Draft'),errorText,
    read:()=>Object.fromEntries(rows().map(row=>[row.getAttribute(attribute),{
      judgment:row.querySelector('[data-judgment]').value,
      reason:row.querySelector('[data-reason]').value,
    }])),
    apply:fields=>{
      for(const row of rows()){
        const name=row.getAttribute(attribute);
        const saved=fields[name]||context.existing_review?.fields?.[name]||{};
        row.querySelector('[data-judgment]').value=
          ['correct','defective','unsure'].includes(saved.judgment)?saved.judgment:'';
        row.querySelector('[data-reason]').value=typeof saved.reason==='string'?saved.reason.slice(0,300):'';
      }
    },
  });
}
const workspaceNames=['create','batch','review','history'];
const mobileNavigation=window.matchMedia('(max-width:720px)');
function syncNavigationOrientation(){
  document.querySelector('.workspace-nav [role=tablist]').setAttribute(
    'aria-orientation',mobileNavigation.matches?'horizontal':'vertical');
}
mobileNavigation.addEventListener('change',syncNavigationOrientation);
syncNavigationOrientation();
function showWorkspace(name,{focus=false}={}){
  if(!workspaceNames.includes(name))name='create';
  for(const key of workspaceNames){
    const selected=key===name;
    $('view-'+key).hidden=!selected;
    $('nav-'+key).setAttribute('aria-selected',String(selected));
    $('nav-'+key).tabIndex=selected?0:-1;
  }
  window.history.replaceState(null,'','#'+name);
  window.scrollTo(0,0);
  if(focus)$('view-'+name).focus({preventScroll:true});
}
function showEditorTab(name){
  for(const key of ['content','export','check']){
    const selected=key===name;
    $('editor-'+key).hidden=!selected;
    $('editor-tab-'+key).setAttribute('aria-selected',String(selected));
    $('editor-tab-'+key).tabIndex=selected?0:-1;
  }
}
function wireTabs(selector,activate){
  const tabs=[...document.querySelectorAll(selector)];
  for(const tab of tabs){
    tab.addEventListener('click',()=>activate(tab));
    tab.addEventListener('keydown',event=>{
      if(!['ArrowLeft','ArrowRight','ArrowUp','ArrowDown','Home','End'].includes(event.key))return;
      event.preventDefault();
      const index=tabs.indexOf(tab);
      const target=event.key==='Home'?0:event.key==='End'?tabs.length-1:
        (index+(['ArrowRight','ArrowDown'].includes(event.key)?1:-1)+tabs.length)%tabs.length;
      tabs[target].focus();activate(tabs[target]);
    });
  }
}
wireTabs('[data-workspace]',tab=>{
  showWorkspace(tab.dataset.workspace);
  if(tab.dataset.workspace==='review')$('loadReviewQueue').click();
});
wireTabs('[data-editor-tab]',tab=>showEditorTab(tab.dataset.editorTab));
window.addEventListener('hashchange',()=>{
  const name=location.hash.slice(1);
  if(workspaceNames.includes(name))showWorkspace(name);
});
function revealPosterPreview(){
  if(window.matchMedia('(max-width:840px)').matches)
    $('preview').scrollIntoView({block:'start'});
}
$('chooseProduct').addEventListener('click',()=>$('create-product').click());
function setBatchStage(stage){
  for(const item of document.querySelectorAll('[data-batch-step]')){
    const step=Number(item.dataset.batchStep);
    item.dataset.state=step<stage?'complete':step===stage?'current':'upcoming';
    if(step===stage)item.setAttribute('aria-current','step');
    else item.removeAttribute('aria-current');
  }
}
function filterBatchRows(){
  const query=$('batchSearch').value.trim().toLowerCase();
  const rows=[...$('batchRows').children];
  for(const row of rows)row.hidden=!row.dataset.sku.includes(query);
  $('batchSearchEmpty').hidden=!rows.length||rows.some(row=>!row.hidden);
}
$('batchSearch').addEventListener('input',filterBatchRows);
const form = $('createForm');
const field = name => form.elements.namedItem(name);
let draftStorage;
try {draftStorage=window.localStorage;} catch {draftStorage=null;}
const createDraftStore=PosterCreateDraft.createStore(draftStorage);
const createDraftFields=['title','subtitle','price','original_price','cta','style','layout_id','product_align','product_scale'];
let draftPreview=createDraftStore.read(),draftAutoSave=draftPreview.state==='empty';
let draftDirty=false,draftTimer=null,draftRestoring=false;
function createDraftSnapshot(){
  return {fields:Object.fromEntries(createDraftFields.map(name=>[name,field(name).value])),
    brief:$('briefText').value,
    files:{product:field('product').files[0]?.name||'',logo:field('logo').files[0]?.name||''}};
}
function draftTime(timestamp){return new Intl.DateTimeFormat('zh-CN',{
  month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'
}).format(new Date(timestamp));}
function showDraftState(result,message=''){
  const status=$('createDraftStatus');
  $('restoreCreateDraft').hidden=!result.draft;
  $('discardCreateDraft').hidden=['empty','unavailable'].includes(result.state);
  status.className='tip';
  if(message){status.textContent=message;return;}
  if(result.state==='available')status.textContent='发现 '+draftTime(result.draft.savedAt)+' 的草稿。恢复将替换当前文案与版式，并使用免费背景；图片需重新选择。';
  else if(result.state==='unavailable')status.textContent='浏览器暂时无法保存草稿，请保留当前页面。你仍可继续制作海报。';
  else if(result.state==='invalid')status.textContent='已存草稿格式无法读取。可删除后重新保存，当前填写内容不受影响。';
  else status.textContent='文案与版式会自动保存在此浏览器，图片需重新选择。';
}
function persistCreateDraft(explicit=false){
  clearTimeout(draftTimer);
  if(!explicit&&(!draftDirty||!draftAutoSave))return;
  if(explicit){createDraftStore.accept();draftAutoSave=true;}
  const result=createDraftStore.save(createDraftSnapshot());
  if(result.state==='saved'){
    draftDirty=false;draftPreview=createDraftStore.read();
    showDraftState(draftPreview,'已于 '+draftTime(result.draft.savedAt)+' 保存文案与版式。图片保留文件名，恢复时需重新选择。');
    $('restoreCreateDraft').hidden=true;
  }else if(result.state==='conflict'){
    draftAutoSave=false;draftPreview=createDraftStore.read();
    showDraftState(draftPreview,'另一页面更新了草稿，自动保存已暂停。可恢复已存草稿，或点击“保存当前进度”保留此页内容。');
  }else showDraftState(result,result.state==='invalid'?'内容过长或格式有误，草稿未保存。请检查输入后重试。':'');
}
function queueCreateDraft(){
  if(draftRestoring)return;
  draftDirty=true;clearTimeout(draftTimer);
  if(!draftAutoSave){
    showDraftState(draftPreview,'当前填写尚未保存。可恢复已存草稿，或点击“保存当前进度”替换它。');
    return;
  }
  $('createDraftStatus').textContent='正在保存文案与版式…';
  draftTimer=setTimeout(()=>persistCreateDraft(),500);
}
form.addEventListener('input',queueCreateDraft);
form.addEventListener('change',queueCreateDraft);
$('briefText').addEventListener('input',queueCreateDraft);
$('saveCreateDraft').addEventListener('click',()=>persistCreateDraft(true));
$('restoreCreateDraft').addEventListener('click',()=>{
  if($('generate').disabled||$('compileBrief').disabled){
    $('createDraftStatus').textContent='当前任务正在处理，请完成后再恢复草稿。';return;
  }
  const latest=createDraftStore.read();
  if(latest.raw!==draftPreview.raw){draftPreview=latest;showDraftState(latest,'已存草稿刚刚变化，请再次点击恢复以使用最新内容。');return;}
  if(!latest.draft){showDraftState(latest);return;}
  clearTimeout(draftTimer);draftRestoring=true;
  resetReview();
  for(const name of createDraftFields)if(Object.hasOwn(latest.draft.fields,name))field(name).value=latest.draft.fields[name];
  $('briefText').value=latest.draft.brief;
  field('background_mode').value='local';
  field('product').value='';field('logo').value='';
  field('product').dispatchEvent(new Event('change',{bubbles:true}));
  $('createCampaignPreset').value='';$('applySingleCampaignPreset').disabled=true;
  syncStylePresetPicker();
  createDraftStore.accept();draftAutoSave=true;draftDirty=false;draftRestoring=false;
  const file=latest.draft.files.product;
  showDraftState(latest,'草稿已恢复，已使用免费配色背景。'+(file?'请重新选择商品图片“'+file+'”。':'请选择商品图片后继续制作。'));
  $('restoreCreateDraft').hidden=true;field('title').focus({preventScroll:true});
});
$('discardCreateDraft').addEventListener('click',()=>{
  const result=createDraftStore.discard();
  if(result.state==='conflict'){
    draftPreview=createDraftStore.accept();draftAutoSave=false;
    showDraftState(draftPreview,'草稿已被其他页面更新，请核对后再次点击删除。');return;
  }
  if(result.state==='empty'){
    clearTimeout(draftTimer);draftDirty=false;draftAutoSave=true;draftPreview=createDraftStore.read();
    showDraftState(result,'已删除浏览器中的草稿。当前表单仍保留，继续编辑会保存新的草稿。');
  }else showDraftState(result);
});
window.addEventListener('storage',event=>{
  if(event.key!==PosterCreateDraft.KEY&&event.key!==null)return;
  clearTimeout(draftTimer);draftAutoSave=false;draftPreview=createDraftStore.read();
  showDraftState(draftPreview,'另一页面修改了已存草稿。当前内容保持不变，请选择恢复或保存当前进度。');
});
document.addEventListener('visibilitychange',()=>{if(document.hidden)persistCreateDraft();});
window.addEventListener('pagehide',()=>persistCreateDraft());
showDraftState(draftPreview);

let productObjectUrl=null;
field('product').addEventListener('change',()=>{resetCutoutPreview();
  if(productObjectUrl)URL.revokeObjectURL(productObjectUrl);
  productObjectUrl=null;
  const input=field('product'),file=input.files[0];
  const selection=$('productSelection'),image=$('productThumb');
  selection.hidden=true;
  input.setCustomValidity('');input.removeAttribute('aria-invalid');
  $('productFileError').textContent='';
  if(!file)return;
  function invalid(message){
    input.setCustomValidity(message);input.setAttribute('aria-invalid','true');
    $('productFileError').textContent=message;selection.hidden=true;
  }
  if(file.size>10*1024*1024){invalid('图片超过 10 MB，请选择较小的 PNG、JPG 或 WEBP 文件。');return;}
  const objectUrl=URL.createObjectURL(file);
  productObjectUrl=objectUrl;
  image.onload=()=>{
    if(productObjectUrl!==objectUrl)return;
    if(image.naturalWidth<64||image.naturalHeight<64||image.naturalWidth>8192||
       image.naturalHeight>8192||image.naturalWidth*image.naturalHeight>16000000){
      invalid('图片尺寸需至少 64×64，单边不超过 8192 像素，总像素不超过 1600 万。');return;
    }
    $('productFileName').textContent=file.name+' · '+image.naturalWidth+'×'+image.naturalHeight;
    selection.hidden=false;
  };
  image.onerror=()=>{if(productObjectUrl===objectUrl)invalid('无法读取图片，请重新选择有效的 PNG、JPG 或 WEBP 文件。');};
  image.src=objectUrl;
});
// Versioned template-layer controls: local pixel offsets, not arbitrary HTML.
const LAYER_BOUNDS={"title_dx":[-20,20],"title_dy":[-20,20],"price_dx":[-12,12],"price_dy":[-8,8],"cta_dx":[-16,16],"cta_dy":[-12,12],"logo_dx":[-16,16],"logo_dy":[-16,16]};
function readLayerLayout(prefix){
  const layout={schema_version:1};
  for(const [name,[min,max]] of Object.entries(LAYER_BOUNDS)){
    const input=$(prefix+'-layer-'+name);
    const raw=input.value.trim();
    const num=Number(raw);
    if(raw==='' || !Number.isInteger(num) || num<min || num>max){
      throw Error(name+' 超出允许的图层偏移范围（'+min+'～'+max+' px）');
    }
    layout[name]=num;
  }
  return layout;
}
function writeLayerLayout(prefix,raw){
  const data=raw||{};
  for(const key of Object.keys(LAYER_BOUNDS)){
    $(prefix+'-layer-'+key).value=String(data[key] ?? 0);
  }
}
// Inspect once in a read-only API, and apply a suggestion only if every form
// value still matches the inspected snapshot. Never mutate an approved Brief.
const layoutInspections={create:null,edit:null};
function specForLayoutInspection(prefix){
  if(prefix==='create')return currentSpec();
  if(!current)throw Error('请先生成或打开海报');
  return {
    ...current.spec,
    title:$('editTitle').value,
    subtitle:$('editSubtitle').value,
    price:$('editPrice').value,
    original_price:$('editOriginal').value||null,
    cta:$('editCta').value,
    layout_id:$('editLayout').value,
    product_align:$('editProductAlign').value,
    product_scale:$('editProductScale').value,
    product_source_mode:$('editProductSourceMode').value,
    layer_layout:readLayerLayout('edit'),
  };
}
for(const prefix of ['create','edit']){
  const label=prefix==='create'?'Create':'Edit';
  $('check'+label+'Layout').addEventListener('click',async()=>{
    const button=$('check'+label+'Layout'),status=$(prefix+'LayoutStatus');
    layoutInspections[prefix]=null;
    $('fix'+label+'Layout').disabled=true;
    button.disabled=true;
    status.className='tip';
    status.textContent='正在检测真实字体的图层边界…';
    try{
      const spec=specForLayoutInspection(prefix);
      const fingerprint=JSON.stringify(spec);
      const response=await fetch('/api/layout/inspect',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:fingerprint,
      });
      const report=await response.json();
      if(!response.ok)throw Error(errorText(report));
      if(JSON.stringify(specForLayoutInspection(prefix))!==fingerprint){
        status.textContent='表单已变更，请重新检测。';return;
      }
      if(report.status==='PASS'){
        status.textContent='图层几何检查通过。仍需对实际商品图与 Logo 做人工检查。';
        status.className='tip good';
      }else{
        const details=report.issues.map(issue=>issue.message).join('；');
        status.textContent='发现遮挡：'+details+
          (report.repair.status==='REPAIRED'?'。可应用受控的最小纵向偏移建议。'
            :'。当前安全区内无可用修正，请缩短文案或更换版式。');
        status.className='tip bad';
        if(report.repair.status==='REPAIRED'){
          layoutInspections[prefix]={
            fingerprint,offsets:report.repair.proposed_layer_layout,
          };
          $('fix'+label+'Layout').disabled=false;
        }
      }
    }catch(err){
      status.textContent='检测失败：'+err.message;status.className='tip bad';
    }finally{button.disabled=false;}
  });
  $('fix'+label+'Layout').addEventListener('click',()=>{
    const status=$(prefix+'LayoutStatus');
    if(prefix==='create' && activeReviewRunId){
      status.textContent='当前存在 Brief 审核任务。先清除任务并重新确认规格后才可修改图层。';
      status.className='tip bad';return;
    }
    const proposal=layoutInspections[prefix];
    if(!proposal)return;
    try{
      if(JSON.stringify(specForLayoutInspection(prefix))!==proposal.fingerprint){
        throw Error('表单已更改，请重新检测再应用修正。');
      }
      writeLayerLayout(prefix,proposal.offsets);
      layoutInspections[prefix]=null;
      $('fix'+label+'Layout').disabled=true;
      status.textContent='已应用安全偏移。请保存新版本并重新执行 OCR 与人工审核。';
      status.className='tip good';
    }catch(err){status.textContent=err.message;status.className='tip bad';}
  });
}

const reviewFields = ['title','subtitle','price','original_price','style','cta','background_mode','layout_id','product_align','product_scale'];
const stylePresetButtons=[...document.querySelectorAll('[data-style-preset]')];
function syncStylePresetPicker(){
  const currentStyle=field('style').value.trim();
  const matched=stylePresetButtons.some(button=>button.dataset.stylePreset===currentStyle);
  for(const button of stylePresetButtons){
    button.setAttribute('aria-pressed',String(button.dataset.stylePreset===currentStyle));
  }
  $('stylePickerStatus').textContent=matched
    ? '已选择「'+currentStyle+'」本地配色；真正生成时仍按背景模式执行。'
    : '当前为自定义风格描述；本地模式按关键词匹配，未匹配将使用默认蓝色。';
}
for(const button of stylePresetButtons){
  button.addEventListener('click',()=>{
    if(activeReviewRunId && activeReviewApproved){
      $('stylePickerStatus').textContent='当前 Brief 已确认。要修改风格，请先点击「清除任务 / 手动填写」后再选择。';
      return;
    }
    field('style').value=button.dataset.stylePreset;
    syncStylePresetPicker();
  });
}
field('style').addEventListener('input',syncStylePresetPicker);
syncStylePresetPicker();
function applyFields(fields) {
  for(const name of reviewFields) {
    if(Object.hasOwn(fields,name) && field(name)) field(name).value = fields[name] ?? '';
  }
  if(!field('style').value) field('style').value = '蓝色科技风';
  if(!field('cta').value) field('cta').value = '立即抢购';
  if(!field('subtitle').value) field('subtitle').value = '品质生活 · 限时优惠';
  if(!field('layout_id').value) field('layout_id').value = 'classic';
  if(!field('product_align').value) field('product_align').value = 'center';
  if(!field('product_scale').value) field('product_scale').value = 'full';
  writeLayerLayout('create', fields.layer_layout);
  syncStylePresetPicker();
}
function showRun(run, restore=false) {
  activeReviewRunId = run.run_id;
  activeReviewApproved = run.status==='APPROVED';
  field('review_run_id').value = run.run_id;
  localStorage.setItem('poster-review-run', run.run_id);
  applyFields(activeReviewApproved ? run.confirmed_spec : run.draft.fields);
  $('approveBrief').classList.toggle('hidden', !run.pending_approval);
  $('rejectBrief').classList.toggle('hidden', !run.pending_approval);
  $('clearBrief').classList.remove('hidden');
  $('runInfo').textContent = '持久化审核 ID：'+run.run_id+' | 状态：'+run.status;
  $('compileWarnings').textContent = '待补充：'+(run.draft.missing_fields.join('、')||'无')+'。'+run.draft.warnings.join('；');
  $('compileStatus').textContent = activeReviewApproved ? '审核已通过，可以生成海报'
    : run.status==='REJECTED' ? '草稿已拒绝，请创建新任务'
    : run.status==='WAITING_INPUT' ? '缺少字段，补充后点击「确认规格」'
    : restore ? '已恢复上次审核任务，请核对后确认' : '草稿已保存，请核对后确认';
  $('compileStatus').className = activeReviewApproved ? 'tip good' : 'tip';
}
function resetReview() {
  activeReviewRunId=null;
  activeReviewApproved=false;
  field('review_run_id').value='';
  localStorage.removeItem('poster-review-run');
  for(const id of ['approveBrief','rejectBrief','clearBrief']) $(id).classList.add('hidden');
  $('compileStatus').textContent='已切换手动填写模式';
  $('compileWarnings').textContent='';
  $('runInfo').textContent='';
  syncStylePresetPicker();
}
$('clearBrief').addEventListener('click', resetReview);
$('compileBrief').addEventListener('click', async () => {
  const brief = $('briefText').value.trim();
  if(brief.length < 8) { $('compileStatus').textContent='Brief 至少输入 8 个字符'; return; }
  $('compileBrief').disabled=true;
  $('compileStatus').textContent='正在编译并保存检查点…';
  try {
    const response = await fetch('/api/briefs/runs',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({brief,compiler:$('briefCompiler').value})
    });
    const json=await response.json();
    if(!response.ok)throw Error(errorText(json));
    showRun(json);
  } catch(err) {
    $('compileStatus').textContent=err.message;
    $('compileStatus').className='tip bad';
  } finally { $('compileBrief').disabled=false; }
});
function currentSpec() {
  const original=field('original_price').value.trim();
  return {
    title:field('title').value,subtitle:field('subtitle').value,
    price:field('price').value,original_price:original||null,
    style:field('style').value,cta:field('cta').value,
    background_mode:field('background_mode').value,
    layout_id:field('layout_id').value,
    product_align:field('product_align').value,
    product_scale:field('product_scale').value,
    product_source_mode:field('product_source_mode').value,
    layer_layout:readLayerLayout('create')
  };
}
async function decide(approved){
  if(!activeReviewRunId)return;
  $('approveBrief').disabled=true;$('rejectBrief').disabled=true;
  try {
    const response=await fetch('/api/briefs/runs/'+activeReviewRunId+'/decision',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(approved ? {approved:true,spec:currentSpec()} : {approved:false})
    });
    const json=await response.json();
    if(!response.ok)throw Error(errorText(json));
    showRun(json);
    $('compileStatus').className=approved?'tip good':'tip';
  } catch(err) {
    $('compileStatus').textContent=err.message;
    $('compileStatus').className='tip bad';
  } finally {$('approveBrief').disabled=false;$('rejectBrief').disabled=false;}
}
$('approveBrief').addEventListener('click',()=>decide(true));
$('rejectBrief').addEventListener('click',()=>decide(false));
(async () => {
  const last=localStorage.getItem('poster-review-run');
  if(!last)return;
  try {
    const response=await fetch('/api/briefs/runs/'+last);
    if(!response.ok){resetReview();return;}
    showRun(await response.json(),true);
  } catch(e){$('compileStatus').textContent='恢复审核任务失败，可刷新重试';}
})();
async function refreshExecutionStatus(keyOverride=null){
  let saved=null;
  try{saved=JSON.parse(localStorage.getItem('poster-last-execution')||'null')}catch(e){}
  const key=keyOverride || saved?.key;
  if(!key){$('executionStatus').textContent='尚无生成执行记录';return;}
  if(!keyOverride && saved?.status==='REJECTED'){
    $('executionStatus').textContent='上次请求在执行前被拒绝，没有创建执行记录；请按上方错误提示修正输入或等待限额窗口后重试。';
    return;
  }
  try{
    const res=await fetch('/api/executions/'+encodeURIComponent(key));
    const obj=await res.json();
    if(!res.ok)throw Error(errorText(obj));
    // Update only the execution that is still current; avoid racing a newer submit.
    let latest=null;
    try{latest=JSON.parse(localStorage.getItem('poster-last-execution')||'null')}catch(e){}
    if(latest?.key===key){
      localStorage.setItem('poster-last-execution',JSON.stringify({...latest,status:obj.status}));
    }
    const hint=obj.status==='UNKNOWN'?'：结果未知，请确认服务端记录后再决定是否新建生成'
      :obj.status==='RUNNING'?'：可能正在调用生图模型，请勿重复提交':'：已有已完成结果；再次主动生成会创建新执行';
    $('executionStatus').textContent='执行 '+key+' · '+obj.status+hint;
  }catch(err){$('executionStatus').textContent='读取执行状态失败：'+err.message;}
}
async function restoreExecutionPoster(posterId){
  try{
    const res=await fetch('/api/posters/'+encodeURIComponent(posterId));
    const obj=await res.json();
    if(!res.ok)throw Error(errorText(obj));
    display(obj);
    showWorkspace('create',{focus:true});
    showEditorTab('content');
    revealPosterPreview();
    $('createStatus').textContent='已从执行历史恢复海报 '+posterId;
    $('createStatus').className='tip good';
  }catch(err){
    $('executionStatus').textContent='恢复海报失败：'+err.message;
  }
}
async function reconcileExecution(key){
  $('executionStatus').textContent='正在核验本地海报文件（不会再次调用生图模型）…';
  try{
    const res=await fetch('/api/executions/'+encodeURIComponent(key)+'/reconcile',{
      method:'POST'
    });
    const obj=await res.json();
    if(!res.ok)throw Error(errorText(obj));
    // If the repaired execution is the current request, update its persisted
    // status so a deliberate next generation gets a new idempotency key.
    let saved=null;
    try{saved=JSON.parse(localStorage.getItem('poster-last-execution')||'null')}catch(e){}
    if(saved?.key===key){
      localStorage.setItem('poster-last-execution',
        JSON.stringify({...saved,status:obj.status}));
    }
    if(obj.status==='COMPLETED' && obj.result?.poster_id){
      display(obj.result);
      $('executionStatus').textContent='已从完整本地产物恢复执行 '+key;
    }else{
      $('executionStatus').textContent='未找到完整匹配的本地产物；状态仍为 '+
        obj.status+'，不会自动重复付费生图';
    }
    await refreshExecutionHistory();
  }catch(err){
    $('executionStatus').textContent='本地产物核验失败：'+err.message;
  }
}
const executionHistoryPaging={offset:0,limit:10};
let executionHistoryRequest=0;
async function refreshExecutionHistory(){
  const host=$('executionHistory');
  const request=++executionHistoryRequest;
  const offset=executionHistoryPaging.offset;
  const limit=executionHistoryPaging.limit;
  const status=$('executionHistoryFilter').value;
  host.textContent='正在读取执行历史…';
  $('executionHistoryPrev').disabled=true;
  $('executionHistoryNext').disabled=true;
  $('executionHistoryRange').textContent='读取中…';
  try{
    const query=new URLSearchParams({limit:String(limit),offset:String(offset)});
    if(status)query.set('status',status);
    const res=await fetch('/api/executions?'+query.toString());
    const obj=await res.json();
    if(request!==executionHistoryRequest)return; // a newer filter/page request won
    if(!res.ok)throw Error(errorText(obj));
    host.replaceChildren();
    const items=obj.items||[];
    if(!items.length){
      host.textContent=offset?'当前页没有记录，请返回上一页':'没有符合条件的执行记录';
    }
    for(const item of items){
      const row=document.createElement('div');
      row.className='execution-row';
      const summary=document.createElement('span');
      summary.className='summary';
      const posterInfo=item.poster_id
        ? ' · 海报 '+item.poster_id.slice(0,8)+'… v'+item.version
        : '';
      summary.textContent=item.status+' · '+item.idempotency_key+' · '+item.updated_at+posterInfo;
      row.appendChild(summary);
      const actions=document.createElement('span');
      actions.className='actions';
      const check=document.createElement('button');
      check.type='button';
      check.className='secondary';
      check.textContent='查状态';
      check.addEventListener('click',()=>refreshExecutionStatus(item.idempotency_key));
      actions.appendChild(check);
      if(item.status==='UNKNOWN'){
        const reconcile=document.createElement('button');
        reconcile.type='button';
        reconcile.className='secondary';
        reconcile.textContent='核对本地产物';
        reconcile.addEventListener('click',()=>reconcileExecution(item.idempotency_key));
        actions.appendChild(reconcile);
      }
      if(item.poster_id){
        const restore=document.createElement('button');
        restore.type='button';
        restore.className='secondary';
        restore.textContent='恢复海报';
        restore.addEventListener('click',()=>restoreExecutionPoster(item.poster_id));
        actions.appendChild(restore);
      }
      row.appendChild(actions);
      host.appendChild(row);
    }
    $('executionHistoryPrev').disabled=offset===0;
    $('executionHistoryNext').disabled=!obj.has_more || offset+limit>10000;
    $('executionHistoryRange').textContent=items.length
      ? '第 '+(offset+1)+'–'+(offset+items.length)+' 条'+(obj.has_more?'，还有更多':'')
      : '当前页无记录';
  }catch(err){
    if(request!==executionHistoryRequest)return;
    host.textContent='读取执行历史失败：'+err.message;
    $('executionHistoryPrev').disabled=offset===0;
    $('executionHistoryRange').textContent='读取失败';
  }
}
$('checkExecution').addEventListener('click',()=>{refreshExecutionStatus();refreshExecutionHistory();});
$('refreshExecutionHistory').addEventListener('click',refreshExecutionHistory);
$('executionHistoryFilter').addEventListener('change',()=>{
  executionHistoryPaging.offset=0;
  refreshExecutionHistory();
});
$('executionHistoryPrev').addEventListener('click',()=>{
  executionHistoryPaging.offset=Math.max(0,executionHistoryPaging.offset-executionHistoryPaging.limit);
  refreshExecutionHistory();
});
$('executionHistoryNext').addEventListener('click',()=>{
  executionHistoryPaging.offset=Math.min(10000,executionHistoryPaging.offset+executionHistoryPaging.limit);
  refreshExecutionHistory();
});
refreshExecutionStatus();
refreshExecutionHistory();

$('cutoutPreviewButton').addEventListener('click',async()=>{
  const input=field('product'),file=input.files[0],status=$('cutoutStatus');
  if(!file){status.textContent='请先选择商品图。';status.className='tip bad';return;}
  resetCutoutPreview();$('cutoutPreviewButton').disabled=true;
  status.className='tip';status.textContent='正在运行本地 u2netp 预览，请人工检查结果…';
  try{
    const body=new FormData();body.append('product',file,file.name);
    const response=await fetch('/api/product-cutouts/preview',{method:'POST',body});
    if(!response.ok)throw Error(errorText(await response.json()));
    const blob=await response.blob();
    if(input.files[0]!==file)throw Error('商品已变更，请重新预览');
    cutoutCandidate=new File([blob],'product-cutout.png',{type:'image/png'});
    cutoutSourceFile=file;
    cutoutOriginalUrl=URL.createObjectURL(file);cutoutResultUrl=URL.createObjectURL(blob);
    $('cutoutOriginalPreview').src=cutoutOriginalUrl;
    $('cutoutResultPreview').src=cutoutResultUrl;
    $('cutoutCompare').hidden=false;
    $('acceptCutout').disabled=false;$('resetCutout').disabled=false;
    status.textContent='请对比原图与透明结果；只在轮廓完整时明确点击采用。';
  }catch(err){status.textContent='抠图失败：'+err.message+'。原图仍可生成。';status.className='tip bad';}
  finally{$('cutoutPreviewButton').disabled=false;}
});
$('acceptCutout').addEventListener('click',()=>{
  if(!cutoutCandidate||field('product').files[0]!==cutoutSourceFile)return;
  if(activeReviewRunId&&activeReviewApproved){
    $('cutoutStatus').textContent='已有确认的 Brief，请先清除审批并重新确认素材规格。';
    $('cutoutStatus').className='tip bad';return;
  }
  field('product_source_mode').value='cutout';
  $('cutoutStatus').textContent='已采用抠图候选。将同时保存原图与抠图；后续版本可切换。';
  $('cutoutStatus').className='tip good';
});
$('resetCutout').addEventListener('click',()=>{
  field('product_source_mode').value='original';
  $('cutoutStatus').textContent='已选择保留原图。本次不会上传抠图候选。';
  $('cutoutStatus').className='tip';
});
async function fingerprintFormData(body){
  const entries=[];
  for(const [name,value] of body.entries()){
    if(value instanceof File){
      const digest=await crypto.subtle.digest('SHA-256',await value.arrayBuffer());
      const sha256=Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('');
      entries.push([name,{name:value.name,size:value.size,type:value.type,sha256}]);
    }else{
      entries.push([name,value]);
    }
  }
  return JSON.stringify(entries);
}
function errorText(json){const d=json.detail; if(Array.isArray(d))return d.map(e=>e.msg).join('；');return typeof d==='string'?d:'请求失败'}
function display(data){
  current=data;
  resetNativeCompare(); updateChannelExport();
  $('overlayPanel').classList.add('hidden');
  $('overlayPreview').removeAttribute('src');
  localStorage.setItem('poster-last-id',data.poster_id);
  loadHumanContext();
  $('gateStatus').textContent='当前版本尚未重新验收';
  $('gateResult').textContent='暂无报告';
  $('previewEmpty').hidden=true;
  $('preview').hidden=false;
  $('preview').src=data.image_url+'?t='+Date.now();
  $('source').textContent=data.background_mode==='ai'?'AI 背景':'本地背景';
  $('metadata').textContent=data.spec.title+' · v'+data.version+' · 已保存在本机';
  $('editor').classList.remove('hidden');
  $('editTitle').value=data.spec.title;
  $('editSubtitle').value=data.spec.subtitle ?? '';
  $('editPrice').value=data.spec.price;
  $('editOriginal').value=data.spec.original_price ?? '';
  $('editCta').value=data.spec.cta;
  $('editLayout').value=data.spec.layout_id ?? 'classic';
  $('editProductAlign').value=data.spec.product_align ?? 'center';
  $('editProductScale').value=data.spec.product_scale ?? 'full';
  writeLayerLayout('edit',data.spec.layer_layout);
  $('editProductSourceMode').value=data.spec.product_source_mode ?? 'original';
  $('editProductSourceMode').querySelector('[value="cutout"]').disabled=!data.product_cutout_available;
  populateSavedLayoutPicker(data);
  $('download').href=data.image_url;
  $('download').download='poster-v'+data.version+'.png';
  $('history').textContent='历史版本：'+data.versions.map(v=>'v'+v).join('、');
  populateHistoryVersions(data)}
let historyPreviewRequest=0;
function populateHistoryVersions(data){
  const select=$('historyVersionSelect');
  const selected=select.value;
  select.replaceChildren();
  for(const version of [...data.versions].reverse()){
    const option=document.createElement('option');
    option.value=String(version);
    option.textContent='v'+version+(version===data.version?'（当前最新）':'');
    select.appendChild(option);
  }
  if(data.versions.includes(Number(selected)))select.value=selected;
  else select.value=String(data.version);
  $('historyVersionPreview').classList.add('hidden');
  $('historyVersionPreview').removeAttribute('src');
  $('historyVersionDownload').classList.add('hidden');
  $('historyVersionStatus').textContent='选择版本后可查看当时的文案和图片。当前编辑表单仍对应 v'+data.version+'。';
}
$('viewHistoryVersion').addEventListener('click',async()=>{
  if(!current)return;
  const posterId=current.poster_id;
  const requestedVersion=Number($('historyVersionSelect').value);
  const request=++historyPreviewRequest;
  $('historyVersionStatus').textContent='正在读取历史版本…';
  try{
    const response=await fetch('/api/posters/'+posterId+'/versions/'+requestedVersion);
    const data=await response.json();
    if(!response.ok)throw Error(errorText(data));
    if(request!==historyPreviewRequest || current?.poster_id!==posterId)return;
    $('historyVersionPreview').src=data.image_url+'?t='+Date.now();
    $('historyVersionPreview').classList.remove('hidden');
    $('historyVersionDownload').href=data.image_url;
    $('historyVersionDownload').download='poster-v'+data.version+'.png';
    $('historyVersionDownload').classList.remove('hidden');
    $('historyVersionStatus').textContent='只读历史 v'+data.version+'：'+data.spec.title+' · 活动价 '+data.spec.price+' · CTA '+data.spec.cta+'。当前编辑表单仍对应最新 v'+current.version+'。';
  }catch(err){
    if(request!==historyPreviewRequest)return;
    $('historyVersionStatus').textContent='读取历史版本失败：'+err.message;
  }
});
// M5i: import the M5g result journal in the browser, never POST it.
// Only SKU and poster IDs enter the ephemeral in-memory review filter.
let batchReviewScope=null;
let lastReviewQueueData=null;
const BATCH_SHA=/^[0-9a-f]{64}$/;
const BATCH_POSTER_ID=/^[0-9a-f]{32}$/;
const BATCH_SKU=/^[A-Za-z0-9_-]{2,64}$/;
function parseBatchReviewJournal(raw){
  const data=JSON.parse(raw);
  if(!data || data.schema_version!=='m5g-1' || data.mode!=='apply' ||
     !BATCH_SHA.test(data.manifest_sha256) || !data.rows ||
     typeof data.rows!=='object' || Array.isArray(data.rows))throw Error('不是有效的 M5g 已导入结果 JSON');
  // Mirror the M5k server-side completeness gate. A partial import journal
  // must not appear as a complete batch in the human review interface.
  const expected=data.expected_sku_ids;
  if(data.completed!==true || !Number.isInteger(data.expected_sku_count) ||
     data.expected_sku_count<1 || data.expected_sku_count>100 ||
     !Array.isArray(expected) || expected.length!==data.expected_sku_count ||
     expected.some(sku=>typeof sku!=='string'||!BATCH_SKU.test(sku)) ||
     new Set(expected).size!==expected.length)
    throw Error('批次尚未完整导入；请重新运行 M5g --apply 后再选择账本');
  const entries=Object.entries(data.rows);
  if(entries.length!==expected.length ||
     entries.some(([sku])=>!expected.includes(sku)))
    throw Error('批次导入记录与预期 SKU 清单不一致，拒绝缩小审核范围');
  const seen=new Set(), scoped=new Map();
  for(const [sku, record] of entries){
    if(!BATCH_SKU.test(sku) || !record || !BATCH_POSTER_ID.test(record.poster_id) ||
       record.version!==1 || !BATCH_SHA.test(record.product_sha256) ||
       !BATCH_SHA.test(record.request_sha256) || seen.has(record.poster_id))
       throw Error('批次数据缺失、重复或格式非法');
    seen.add(record.poster_id);
    scoped.set(record.poster_id,sku);
  }
  return {ids:scoped, manifest_sha256:data.manifest_sha256};
}
function renderReviewQueue(data){
  lastReviewQueueData=data;
  $('reviewQueueItems').replaceChildren();
  const listed=batchReviewScope ? data.items.filter(x=>batchReviewScope.ids.has(x.poster_id)) : data.items;
  const missing=batchReviewScope ? batchReviewScope.ids.size-listed.length : 0;
  $('reviewQueueStatus').textContent=(batchReviewScope?'本批次 ':'全部 ')+listed.length+
    ' 张待检海报（总库 '+data.total_posters+' 张，版式：'+
    (data.variant_id==='feed_4x5_native_product_focus'?'商品主体优先':'经典分栏')+
    '）；按待处理状态排序。'+
    (missing ? '本批次有 '+missing+' 个 ID 不在当前海报库，请核对 POSTER_DATA_DIR。':'');
  for(const item of listed){
    const row=document.createElement('div');
    row.className='actions';
    row.style.cssText='border-top:1px solid #e6eaf1;padding:10px 0;justify-content:space-between';
    const text=document.createElement('div');text.style.cssText='flex:1;min-width:170px';
    const label=document.createElement('div');
    const sku=batchReviewScope?.ids.get(item.poster_id);
    label.textContent=(sku?'SKU '+sku+' · ':'')+item.title+' · v'+item.version;
    const hint=document.createElement('div');hint.className='tip';
    hint.textContent=(item.status==='READY'
      ? '证据齐备（可按此版式单独评分，非投放批准）'
      : (QUEUE_STATUS_LABELS[item.status]||item.status))+' · '+item.action;
    text.append(label,hint);
    const open=document.createElement('button');
    open.type='button';open.className='secondary';open.textContent='打开审核';
    open.addEventListener('click',async()=>{
      open.disabled=true;
      try{
        const res=await fetch(item.poster_url);const poster=await res.json();
        if(!res.ok)throw Error(errorText(poster));
        if(poster.version!==item.version)throw Error('该海报已有新版本，请刷新队列');
        $('channelPreset').value=data.variant_id;display(poster);
        showWorkspace('create',{focus:true});showEditorTab('export');
        $('channelFormats').querySelector('details').open=true;
        $('preview').scrollIntoView({behavior:'smooth',block:'start'});
        $('reviewQueueStatus').textContent='已打开 '+(sku||item.title)+' · v'+item.version+
          '；核对 4:5 预览后进行人工标注。';
      }catch(e){$('reviewQueueStatus').textContent='打开失败：'+e.message;}
      finally{open.disabled=false;}
    });
    row.append(text,open);$('reviewQueueItems').append(row);
  }
}
$('batchReviewFile').addEventListener('change',async()=>{
  const file=$('batchReviewFile').files?.[0];
  if(!file)return;
  // Local-only parse: no file upload and no HTML insertion.
  try{
    if(file.size>200000)throw Error('批次文件超过 200 KB，拒绝解析');
    const scope=parseBatchReviewJournal(await file.text());
    batchReviewScope=scope;
    $('batchReviewScopeStatus').textContent='仅看 M5g 批次：'+scope.ids.size+
      ' 个 SKU（清单 SHA-256：'+scope.manifest_sha256.slice(0,12)+'…）。本地浏览器筛选，未上传文件。';
    if(lastReviewQueueData)renderReviewQueue(lastReviewQueueData);
  }catch(e){
    $('batchReviewScopeStatus').textContent='批次筛选未更改：'+e.message;
  }finally{$('batchReviewFile').value='';}
});
$('clearBatchScope').addEventListener('click',()=>{
  batchReviewScope=null;
  $('batchReviewScopeStatus').textContent='当前查看全部本地海报。';
  if(lastReviewQueueData)renderReviewQueue(lastReviewQueueData);
});
const QUEUE_STATUS_LABELS={
  NEEDS_REVIEW:'待人工审核',REVIEW_UNSURE:'人工无法判断',
  OCR_MISSING:'缺 OCR 报告',OCR_NOT_OK:'OCR 未成功',OCR_STALE:'OCR 报告过期',
  OCR_CHECKS_INVALID:'OCR 证据不完整',REVIEW_STALE:'人工审核过期',
  SOURCE_INVALID:'源素材/版本异常',READY:'可评分（非投放批准）'
};
$('reviewQueueVariant').addEventListener('change',()=>{
  lastReviewQueueData=null;
  $('reviewQueueItems').replaceChildren();
  $('reviewQueueStatus').textContent='版式已切换，请刷新验收队列；人工审核和 OCR 证据不跨版式继承。';
});
$('loadReviewQueue').addEventListener('click',async()=>{
  const button=$('loadReviewQueue');
  const reviewer=$('nativeReviewer').value.trim();
  const variant=$('reviewQueueVariant').value;
  button.disabled=true;
  $('reviewQueueStatus').textContent='正在盘点已保存的原生版本；不会触发 OCR…';
  $('reviewQueueItems').replaceChildren();
  try{
    const response=await fetch('/api/trials/review-queue?reviewer='+encodeURIComponent(reviewer)+'&variant='+encodeURIComponent(variant));
    const data=await response.json();
    if(!response.ok)throw Error(errorText(data));
    if(reviewer!==$('nativeReviewer').value.trim() ||
       variant!==$('reviewQueueVariant').value || data.variant_id!==variant)return;
    renderReviewQueue(data);
  }catch(e){$('reviewQueueStatus').textContent='读取队列失败：'+e.message;}
  finally{button.disabled=false;}
});
let nativeReviewContext=null;
let nativeDraftSession=null;
let nativeReviewRequest=0;
const NATIVE_VISUAL_LABELS={
  sku_integrity:'商品完整/无遮挡',text_legibility:'整体文字可读性',composition:'画面构图与层次',
  title:'主标题',price:'活动价',cta:'CTA',subtitle:'副标题',original_price:'原价'
};
$('nativeReviewer').value=localStorage.getItem('poster-native-reviewer')||'local-operator';
function nativeHumanUrl(posterId,version,variant){
  return '/api/posters/'+posterId+'/versions/'+version+'/exports/'+variant+'/human-review';
}
function nativeReviewExportUrl(){
  const productFocus=$('channelPreset').value==='feed_4x5_native_product_focus';
  const path=productFocus
    ? '/api/annotations/native-4x5-product-focus/export.csv'
    : '/api/annotations/native-4x5/export.csv';
  $('exportNativeReview').href=path+'?reviewer='+
    encodeURIComponent($('nativeReviewer').value.trim());
  $('exportNativeReview').download=productFocus
    ? 'poster-native-product-focus-4x5-labels.csv'
    : 'poster-native-4x5-labels.csv';
}
async function loadNativeReviewContext(){
  const request=++nativeReviewRequest;
  nativeDraftSession?.close();nativeDraftSession=null;
  nativeReviewContext=null;
  $('saveNativeReview').disabled=true;
  $('nativeReviewFields').replaceChildren();
  nativeReviewExportUrl();
  const variant=$('channelPreset').value;
  if(!current || !['feed_4x5_native','feed_4x5_native_product_focus'].includes(variant)){
    $('nativeHumanPanel').classList.add('hidden');
    return;
  }
  $('nativeHumanPanel').classList.remove('hidden');
  const posterId=current.poster_id,version=current.version,reviewer=$('nativeReviewer').value.trim();
  $('nativeReviewStatus').textContent='正在读取实际 4:5 海报的摘要…';
  try{
    const url=nativeHumanUrl(posterId,version,variant)+'?reviewer='+encodeURIComponent(reviewer);
    const response=await fetch(url);
    const info=await response.json();
    if(!response.ok)throw Error(errorText(info));
    if(request!==nativeReviewRequest || !current || current.poster_id!==posterId || current.version!==version ||
       $('channelPreset').value!==variant || reviewer!==$('nativeReviewer').value.trim())return;
    nativeReviewContext=info;
    for(const key of info.fields_to_review){
      const row=document.createElement('div');
      row.className='row';
      row.dataset.nativeField=key;
      const label=document.createElement('label');
      label.textContent=NATIVE_VISUAL_LABELS[key]||key;
      const select=document.createElement('select');
      select.dataset.judgment='1';
      for(const [value,title] of [
        ['','请选择'],['correct','图片正确'],['defective','图片有缺陷'],['unsure','无法判断']
      ]){const option=document.createElement('option');option.value=value;option.textContent=title;select.append(option);}
      const reason=document.createElement('input');
      reason.dataset.reason='1';reason.maxLength=300;reason.placeholder='错误位置 / 理由（可选）';
      const existing=info.existing_review?.fields?.[key];
      if(existing){select.value=existing.judgment;reason.value=existing.reason||'';}
      row.append(label,select,reason);
      $('nativeReviewFields').append(row);
    }
    $('nativeReviewStatus').textContent='v'+version+' · '+
      (variant==='feed_4x5_native_product_focus'?'商品主体优先':'经典分栏')+' · '+
      info.review_count+' 条人工审核记录（'+info.distinct_reviewers+' 位代号）；不自动放行';
    const draft=reviewDraftSession(info,variant,reviewer,'nativeReviewFields','data-native-field','native');
    nativeDraftSession=draft;
    const ready=await draft.start();
    if(request===nativeReviewRequest&&nativeDraftSession===draft)$('saveNativeReview').disabled=!ready;
  }catch(e){$('nativeReviewStatus').textContent='无法读取 4:5 审核上下文：'+e.message;}
}
$('nativeReviewer').addEventListener('change',()=>{
  localStorage.setItem('poster-native-reviewer',$('nativeReviewer').value.trim());
  loadNativeReviewContext();
});
$('saveNativeReview').addEventListener('click',async()=>{
  const variant=$('channelPreset').value;
  if(!current || !nativeReviewContext ||
     !['feed_4x5_native','feed_4x5_native_product_focus'].includes(variant))return;
  const posterId=current.poster_id,version=current.version;
  const context=nativeReviewContext,draft=nativeDraftSession;
  const reviewer=$('nativeReviewer').value.trim();
  const fields={};
  for(const row of $('nativeReviewFields').querySelectorAll('[data-native-field]')){
    const key=row.dataset.nativeField;
    const judgment=row.querySelector('[data-judgment]').value;
    if(!judgment){$('nativeReviewStatus').textContent='请审核所有显示的字段：'+(NATIVE_VISUAL_LABELS[key]||key);return;}
    fields[key]={judgment,reason:row.querySelector('[data-reason]').value.trim()};
  }
  if(draft&&!await draft.beginSubmit())return;
  if(nativeReviewContext!==context||nativeDraftSession!==draft)return;
  $('saveNativeReview').disabled=true;
  try{
    const response=await fetch(nativeHumanUrl(posterId,version,variant),{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        reviewer,fields,
        image_sha256:context.image_sha256,
        source_image_sha256:context.source_image_sha256,
        spec_sha256:context.spec_sha256,
        asset_sha256:context.asset_sha256
      })
    });
    const result=await response.json();
    if(!response.ok)throw Error(errorText(result));
    if(current?.poster_id===posterId&&current?.version===version&&
       $('channelPreset').value===variant){
      draft?.close();
      await loadNativeReviewContext();
      $('nativeReviewStatus').textContent='已提交 '+(variant==='feed_4x5_native_product_focus'?'商品主体优先':'经典分栏')+' 4:5 审核 '+result.review_id+'；后续更正会新增记录';
      $('nativeReviewStatus').className='tip good';
    }
  }catch(e){
    $('nativeReviewStatus').textContent='4:5 审核失败：'+e.message;
    $('nativeReviewStatus').className='tip bad';
  }finally{
    draft?.controls(false);
    if(current?.poster_id===posterId&&current?.version===version&&
       $('channelPreset').value===variant&&nativeReviewContext)
      $('saveNativeReview').disabled=false;
  }
});
nativeReviewExportUrl();


const NATIVE_COMPARE_CHOICES=[
  ['feed_4x5_native','nativeCompareSplit','chooseNativeSplit'],
  ['feed_4x5_native_product_focus','nativeCompareFocus','chooseNativeFocus']
];
let nativeCompareGeneration=0;

function resetNativeCompare(){
  nativeCompareGeneration+=1;
  $('nativeCompareGrid').classList.add('hidden');
  $('nativeCompareStatus').textContent='点击后按需生成两个本地排版预览，不调用 AI。';
  for(const [_variant,imageId,buttonId] of NATIVE_COMPARE_CHOICES){
    $(buttonId).disabled=true;
    $(imageId).removeAttribute('src');
  }
}

function showNativeCompare(){
  if(!current)return;
  const posterId=current.poster_id,version=current.version;
  const generation=++nativeCompareGeneration;
  let loaded=0;
  $('nativeCompareGrid').classList.remove('hidden');
  $('nativeCompareStatus').textContent='正在加载同一版本 v'+version+' 的两种本地原生排版…';
  for(const [variant,imageId,buttonId] of NATIVE_COMPARE_CHOICES){
    const image=$(imageId);
    $(buttonId).disabled=true;
    image.onload=()=>{
      if(generation!==nativeCompareGeneration)return;
      $(buttonId).disabled=false;
      loaded+=1;
      if(loaded===NATIVE_COMPARE_CHOICES.length){
        $('nativeCompareStatus').textContent='两个预览已载入，均来自 v'+version+
          '。请选择要导出的模板，并对所选模板单独验收。';
      }
    };
    image.onerror=()=>{
      if(generation!==nativeCompareGeneration)return;
      $(buttonId).disabled=true;
      $('nativeCompareStatus').textContent='有模板预览失败：可能文案超出其排版空间，请缩短文字后生成新版本。';
    };
    image.src='/api/posters/'+posterId+'/versions/'+version+'/exports/'+variant;
  }
}

$('compareNativeLayouts').addEventListener('click',showNativeCompare);
for(const [variant,_imageId,buttonId] of NATIVE_COMPARE_CHOICES){
  $(buttonId).addEventListener('click',()=>{
    if(!current || $(buttonId).disabled)return;
    $('channelPreset').value=variant;
    updateChannelExport();
    $('channelPreview').scrollIntoView({behavior:'smooth',block:'nearest'});
  });
}

function updateChannelExport(){
  if(!current)return;
  const preset=$('channelPreset').value;
  const url='/api/posters/'+current.poster_id+'/versions/'+current.version+'/exports/'+preset;
  const sizes={
    feed_4x5_native:[1080,1350],feed_4x5_native_product_focus:[1080,1350],feed_4x5:[1080,1350],
    story_9x16:[1080,1920],landscape_16x9:[1200,675]
  };
  const dim=sizes[preset]||[1024,1024];
  $('channelPreview').style.aspectRatio=dim[0]+'/'+dim[1];
  $('channelPreview').src=url;
  $('channelDownload').href=url;
  $('channelDownload').download='poster-v'+current.version+'-'+preset+'.png';
  $('channelStatus').textContent=preset==='feed_4x5_native'
    ? '经典原生 4:5：左侧促销文案、右侧商品，独立重新排版；不调用 AI。'
    : preset==='feed_4x5_native_product_focus'
      ? '商品主体优先：上半部突出完整商品，下半部展示促销信息；不调用 AI。此版式需要单独验收。'
      : '安全缩放：方形原图完整保留，背景延展，不重新排版文字。';
  const isBaselineNative=preset==='feed_4x5_native';
  const isProductFocus=preset==='feed_4x5_native_product_focus';
  const isNativeLayout=isBaselineNative||isProductFocus;
  $('checkNative').disabled=!isNativeLayout;
  $('nativeOcrMode').disabled=!isNativeLayout;
  $('nativeGateStatus').textContent=isProductFocus
    ? '商品主体优先版式：OCR 报告与人工标签均独立绑定此版式。'
    : '尚未检查当前 v'+current.version+' 的原生 4:5 导出';
  $('nativeGateResult').textContent=isProductFocus
    ? '尚未检查此版式；报告不会继承其他版式结论，且不代表视觉质量或投放审核通过。'
    : '未检查该导出版本';
  loadNativeReviewContext();
}
$('channelPreset').addEventListener('change',updateChannelExport);
$('checkNative').addEventListener('click', async () => {
  const variant=$('channelPreset').value;
  if(!current || !['feed_4x5_native','feed_4x5_native_product_focus'].includes(variant)) return;
  const id=current.poster_id, version=current.version;
  $('checkNative').disabled=true;
  $('nativeGateStatus').textContent='正在核对原生导出（真实 OCR 可能需要加载模型）…';
  try {
    const res=await fetch('/api/posters/'+id+'/versions/'+version+'/exports/'+variant+'/acceptance',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ocr_mode:$('nativeOcrMode').value})
    });
    const report=await res.json();
    if(!res.ok) throw Error(errorText(report));
    if(!current || current.poster_id!==id || current.version!==version ||
       $('channelPreset').value!==variant) return;
    $('nativeGateStatus').textContent=(variant==='feed_4x5_native_product_focus'?'商品主体优先 4:5':'经典原生 4:5')+'：'+report.auto_checks+' · OCR：'+report.ocr_status+' · '+report.verdict;
    $('nativeGateStatus').className='tip '+(report.auto_checks==='FAIL'?'bad':'');
    $('nativeGateResult').textContent=report.checks.map(c=>
      c.status+' | '+c.name+'：'+c.detail).join('\n')+
      '\n\n图片 SHA-256：'+report.native_image_sha256+
      '\n独立报告：未继承方形版验收结果；'+(variant==='feed_4x5_native_product_focus'?'人工标签与该版式独立绑定。':'仍需人工确认。');
  }catch(e){
    $('nativeGateStatus').textContent='验收失败：'+e.message;
    $('nativeGateStatus').className='tip bad';
  }finally{
    if(current?.poster_id===id && current?.version===version &&
       $('channelPreset').value===variant) $('checkNative').disabled=false;
  }
});

$('createForm').addEventListener('submit',async e=>{e.preventDefault();
  if(activeReviewRunId && !activeReviewApproved){
    $('createStatus').textContent='请先确认规格，或点击「清除任务 / 手动填写」';
    $('createStatus').className='tip bad';
    return;
  }
  const body=new FormData(e.target);
  if(field('product_source_mode').value==='cutout'){
    if(!cutoutCandidate||field('product').files[0]!==cutoutSourceFile){
      $('createStatus').textContent='抠图已失效，请重新预览并选择。';$('createStatus').className='tip bad';return;
    }
    body.append('product_cutout',cutoutCandidate,cutoutCandidate.name);
  }
  try {
    body.set('layer_layout', JSON.stringify(readLayerLayout('create')));
  } catch(err) {
    $('createStatus').textContent='图层参数无效：'+err.message;
    $('createStatus').className='tip bad';
    return;
  }
  let fp;
  try{
    fp=await fingerprintFormData(body);
  }catch(err){
    $('createStatus').textContent='无法计算请求指纹，请刷新页面后重试：'+err.message;
    $('createStatus').className='tip bad';
    return;
  }
  let stored=null;
  try{stored=JSON.parse(localStorage.getItem('poster-last-execution')||'null')}catch(e){}
  // Reuse a key only for an unfinished/ambiguous logical request. Once the
  // prior request is completed, an intentional new submit gets a fresh key.
  const key=(stored && stored.fp===fp && stored.status!=='COMPLETED')
    ? stored.key : crypto.randomUUID();
  localStorage.setItem('poster-last-execution',JSON.stringify({key,fp,status:'PENDING'}));
  $('generate').disabled=true;
  $('createStatus').textContent='正在生成，AI 模式可能耗时较久…';
  $('createStatus').className='tip';
  try{
    const res=await fetch('/api/posters',{method:'POST',
      headers:{'Idempotency-Key':key},body});
    const json=await res.json();
    if(!res.ok){
      // These responses are emitted before the execution claim; no ledger row
      // exists, so don't misreport a missing execution as a recovery failure.
      if([422,428,429].includes(res.status)){
        localStorage.setItem('poster-last-execution',JSON.stringify({key,fp,status:'REJECTED'}));
      }
      throw Error(errorText(json));
    }
    localStorage.setItem('poster-last-execution',JSON.stringify({key,fp,status:'COMPLETED'}));
    display(json);
    revealPosterPreview();
    $('createStatus').textContent='生成成功；主动再次生成会创建新执行，网络中断重试会复用原键';
    $('createStatus').className='tip good';
  }catch(err){
    $('createStatus').textContent=err.message;
    $('createStatus').className='tip bad';
  }finally{
    $('generate').disabled=false;
    refreshExecutionStatus();
    refreshExecutionHistory();
  }
});
$('editForm').addEventListener('submit',async e=>{
  e.preventDefault();
  if(!current)return;
  const posterId=current.poster_id;
  const version=current.version;
  let payload;
  try {payload={
    expected_version:version,
    title:$('editTitle').value,
    subtitle:$('editSubtitle').value,
    price:$('editPrice').value,
    original_price:$('editOriginal').value || null,
    cta:$('editCta').value,
    layout_id:$('editLayout').value,
    product_align:$('editProductAlign').value,
    product_scale:$('editProductScale').value,
    product_source_mode:$('editProductSourceMode').value,
    layer_layout:readLayerLayout('edit'),
  };}catch(err){
    $('editStatus').textContent='图层参数无效：'+err.message;
    $('editStatus').className='tip bad';
    return;
  }
  $('saveEdit').disabled=true;
  $('editStatus').textContent='正在重新排版…';
  $('editStatus').className='tip';
  try {
    const res=await fetch('/api/posters/'+posterId+'/revisions',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(payload),
    });
    const json=await res.json();
    if(res.status===409){
      throw Error('当前海报已有更新版本，修改未保存。请核对后点击「加载最新版本」重新编辑。');
    }
    if(!res.ok)throw Error(errorText(json));
    if(!current || current.poster_id!==posterId)return;
    display(json);
    $('editStatus').textContent='新版本已保存：图层位置、文字和版式已更新；未调用图像模型。请重新验收。';
    $('editStatus').className='tip good';
  } catch(err) {
    $('editStatus').textContent='保存失败：'+err.message;
    $('editStatus').className='tip bad';
  } finally {$('saveEdit').disabled=false;}
});
$('reloadPoster').addEventListener('click',async()=>{
  if(!current)return;
  const posterId=current.poster_id;
  $('reloadPoster').disabled=true;
  try{
    const res=await fetch('/api/posters/'+posterId);
    const json=await res.json();
    if(!res.ok)throw Error(errorText(json));
    if(current?.poster_id===posterId){
      display(json);
      $('editStatus').textContent='已载入最新版本 v'+json.version+'，请重新检查并编辑。';
      $('editStatus').className='tip';
    }
  }catch(err){
    $('editStatus').textContent='读取最新版本失败：'+err.message;
    $('editStatus').className='tip bad';
  }finally{$('reloadPoster').disabled=false;}
});
$('runGate').addEventListener('click', async () => {
  if(!current)return;
  const posterId=current.poster_id, version=current.version;
  $('runGate').disabled=true;
  $('gateStatus').textContent='正在执行验收（PaddleOCR 首次加载可能较慢）…';
  try {
    const response=await fetch('/api/posters/'+posterId+'/versions/'+version+'/acceptance',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({ocr_mode:$('ocrMode').value})
    });
    const report=await response.json();
    if(!response.ok)throw Error(errorText(report));
    if(!current || current.poster_id!==posterId || current.version!==version) return;
    $('gateStatus').textContent='自动检查：'+report.auto_checks+' · 结论：'+report.verdict+' · OCR：'+report.ocr_status;
    $('gateStatus').className='tip '+(report.auto_checks==='FAIL'?'bad':'');
    const items=report.checks.map(c=>c.status+' | '+c.name+'：'+c.detail);
    $('gateResult').textContent=items.join('\n')+'\n\n注：即使自动检查 PASS，仍需人工确认，不能直接投放。';
    // OCR evidence is derived from the saved version's report and does not rerun OCR.
    const overlay='/api/posters/'+posterId+'/versions/'+version
      +'/acceptance/overlay?ocr_mode='+encodeURIComponent(report.ocr_mode);
    $('overlayPreview').src=overlay;
    $('downloadOverlay').href=overlay;
    $('downloadOverlay').download='poster-'+posterId.slice(0,8)+'-v'+version+'-ocr.png';
    $('overlayPanel').classList.remove('hidden');
  } catch(error) {
    $('gateStatus').textContent='验收失败：'+error.message;
    $('gateStatus').className='tip bad';
  } finally {$('runGate').disabled=false;}
});

let humanContext=null;
let humanDraftSession=null;
let humanReviewRequest=0;
const humanLabels=[...document.querySelectorAll('[data-human-field]')];
$('humanReviewer').value=localStorage.getItem('poster-human-reviewer')||'local-operator';
function annotationUrl(){
  return '/api/posters/'+current.poster_id+'/versions/'+current.version+'/human-review';
}
function refreshExportLink(){
  const reviewer=$('humanReviewer').value.trim();
  $('exportHuman').href='/api/annotations/export.csv?reviewer='+encodeURIComponent(reviewer);
}
async function refreshCliBatchJournal(){
  const summary=$('cliBatchJournalSummary');
  const host=$('cliBatchJournalRows');
  summary.textContent='正在读取命令行账本…';
  host.replaceChildren();
  try{
    const response=await fetch('/api/cli-batches/status');
    const state=await response.json();
    if(!response.ok)throw Error(errorText(state));
    if(!state.available){
      summary.textContent=state.message;
      return;
    }
    summary.textContent=(state.completed?'命令行批次已完成':'命令行批次未完成')+
      ' · '+state.completed_sku_count+'/'+state.expected_sku_count+
      ' 个 SKU 完成，待处理 '+state.pending_sku_count;
    for(const item of (state.rows||[])){
      const row=document.createElement('div');
      row.className='execution-row';
      const label=document.createElement('span');
      label.className='summary';
      label.textContent='SKU '+item.sku_id+' · '+
        (item.status==='COMPLETED'?'已导入':'待处理');
      row.appendChild(label);
      host.appendChild(row);
    }
  }catch(err){
    summary.textContent='读取命令行账本失败：'+err.message;
  }
}
$('refreshCliBatchJournal').addEventListener('click',refreshCliBatchJournal);
refreshCliBatchJournal();

async function loadHumanContext(){
  if(!current)return;
  const request=++humanReviewRequest;
  humanDraftSession?.close();humanDraftSession=null;
  humanContext=null;
  $('saveHuman').disabled=true;
  const posterId=current.poster_id,version=current.version;
  humanLabels.forEach(row=>{
    row.querySelector('[data-judgment]').value='';
    row.querySelector('[data-reason]').value='';
    row.hidden=true;
  });
  $('humanStatus').textContent='正在载入当前版本校验值…';
  refreshExportLink();
  try{
    const reviewer=$('humanReviewer').value.trim();
    const response=await fetch(annotationUrl()+'?reviewer='+encodeURIComponent(reviewer));
    const info=await response.json();
    if(!response.ok)throw Error(errorText(info));
    if(request!==humanReviewRequest || !current||current.poster_id!==posterId||current.version!==version ||
       reviewer!==$('humanReviewer').value.trim())return;
    humanContext=info;
    humanLabels.forEach(row=>{
      const name=row.dataset.humanField;
      row.hidden=!info.fields_to_review.includes(name);
      const saved=info.existing_review?.fields?.[name];
      if(saved){
        row.querySelector('[data-judgment]').value=saved.judgment;
        row.querySelector('[data-reason]').value=saved.reason||'';
      }
    });
    $('humanStatus').textContent='v'+version+' · 已有 '+info.review_count+' 条审阅记录（含历史更正）';
    const draft=reviewDraftSession(info,'square',reviewer,'humanFields','data-human-field','human');
    humanDraftSession=draft;
    const ready=await draft.start();
    if(request===humanReviewRequest&&humanDraftSession===draft)$('saveHuman').disabled=!ready;
  }catch(err){$('humanStatus').textContent='无法载入标注上下文：'+err.message;}
}
$('humanReviewer').addEventListener('change',()=>{
  const alias=$('humanReviewer').value.trim();
  if(!/^[a-zA-Z0-9_-]{2,40}$/.test(alias)){
    $('humanStatus').textContent='审核员代号格式无效';
    $('saveHuman').disabled=true;
    return;
  }
  localStorage.setItem('poster-human-reviewer',alias);
  loadHumanContext();
});
$('saveHuman').addEventListener('click',async()=>{
  if(!humanContext||!current)return;
  const context=humanContext,draft=humanDraftSession;
  const reviewer=$('humanReviewer').value.trim();
  if(!/^[a-zA-Z0-9_-]{2,40}$/.test(reviewer)){ $('humanStatus').textContent='审核员代号无效'; return;}
  const fields={};
  for(const row of humanLabels){
    const name=row.dataset.humanField;
    if(!humanContext.fields_to_review.includes(name))continue;
    const judgment=row.querySelector('[data-judgment]').value;
    if(!judgment){$('humanStatus').textContent='请先为所有显示字段选择正确 / 错误 / 无法判断';return;}
    fields[name]={judgment,reason:row.querySelector('[data-reason]').value};
  }
  const posterId=current.poster_id,version=current.version;
  const url=annotationUrl();
  if(draft&&!await draft.beginSubmit())return;
  if(humanContext!==context||humanDraftSession!==draft)return;
  $('saveHuman').disabled=true;
  $('humanStatus').textContent='正在保存附带图片 SHA-256 的人工标签…';
  try{
    const response=await fetch(url,{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({reviewer,image_sha256:context.image_sha256,
        spec_sha256:context.spec_sha256,fields})
    });
    const item=await response.json();
    if(!response.ok)throw Error(errorText(item));
    if(current?.poster_id===posterId&&current?.version===version){
      draft?.close();
      await loadHumanContext();
      $('humanStatus').textContent='已提交不可变标注：'+item.review_id+'。如需更正，可再次提交，历史保留。';
      $('humanStatus').className='tip good';
    }
  }catch(err){
    $('humanStatus').textContent='保存失败：'+err.message;
    $('humanStatus').className='tip bad';
  }finally{
    draft?.controls(false);
    if(current?.poster_id===posterId&&current?.version===version)
      $('saveHuman').disabled=false;
  }
});
const M6_ISSUE_LABELS={
  GOVERNANCE_PENDING:'授权/独立性声明待补',
  MISSING_ROW:'缺少应有的版式评审行',
  OUTPUT_MISSING:'缺少图片引用或有效 SHA-256',
  REVIEW_PENDING:'人工判断、字段标签、审核员或 UTC 时间尚未齐备'
};
$('checkM6Progress').addEventListener('click',async()=>{
  const registry=$('m6RegistryCsv').files[0];
  const reviews=$('m6ReviewsCsv').files[0];
  const summary=$('m6ProgressSummary'),issues=$('m6ProgressIssues');
  issues.replaceChildren();
  if(!registry||!reviews){
    summary.textContent='请先选择 M6 registry 和 output reviews 两个 CSV。';
    return;
  }
  $('checkM6Progress').disabled=true;
  summary.textContent='正在由本地服务解析 CSV，不访问图片和 OCR…';
  try{
    const body=new FormData();
    body.append('registry',registry,registry.name);
    body.append('reviews',reviews,reviews.name);
    const response=await fetch('/api/m6/review-progress',{method:'POST',body});
    const result=await response.json();
    if(!response.ok)throw Error(errorText(result));
    summary.textContent='cohort '+result.cohort_id+' · SKU '+result.sku_total+
      ' · 人工表格齐备 '+result.review_complete+'/'+result.expected_outputs+
      ' · 待补 '+result.review_pending+
      ' · 缺少图片引用 '+result.missing_outputs+
      ' · 已填写授权/独立性声明 '+result.governance_asserted+'/'+result.sku_total+
      '。注意：图片与授权真实性、OCR 均未验证，禁止据此投放。';
    for(const issue of result.issues||[]){
      const row=document.createElement('div');
      row.className='execution-row';
      const item=document.createElement('span');
      item.className='summary';
      item.textContent=issue.sku_id+
        (issue.variant_id?' / '+issue.variant_id:'')+
        ' · '+(M6_ISSUE_LABELS[issue.code]||issue.code);
      row.appendChild(item);
      issues.appendChild(row);
    }
    if(!(result.issues||[]).length){
      issues.textContent='两份 CSV 的人工填写项暂未发现缺口。仍需运行 M6 最终证据门禁与真实 OCR。';
    }
  }catch(err){
    summary.textContent='预览失败：'+err.message;
  }finally{
    $('checkM6Progress').disabled=false;
  }
});

const BATCH_STORAGE_KEY='poster-agent-active-batch';
let currentBatchId=null;
let batchPollTimer=null;
let batchPollGeneration=0;
let lastBatchStatus=null;
function batchStatusLabel(status){
  return ({READY:'已预检，等待开始',RUNNING:'正在生成',COMPLETED:'批次已完成',FAILED:'批次中断，可安全续跑',INTERRUPTED:'服务中断，可安全续跑',UNKNOWN:'状态未知',PENDING:'待处理'})[status]||status;
}
function resetBatchForNewFiles(){
  if(batchPollTimer){clearTimeout(batchPollTimer);batchPollTimer=null;}
  batchPollGeneration+=1;
  currentBatchId=null;
  lastBatchStatus=null;
  localStorage.removeItem(BATCH_STORAGE_KEY);
  batchReviewScope=null;
  $('batchReviewScopeStatus').textContent='当前查看全部本地海报。';
  if(lastReviewQueueData)renderReviewQueue(lastReviewQueueData);
  $('batchResults').hidden=true;
  $('applyBatch').hidden=true;
  $('preflightBatch').classList.remove('secondary');
  $('batchSearch').value='';
  $('batchSearchEmpty').hidden=true;
  setBatchStage(0);
  updateBatchFileSummary();
  $('batchNextAction').textContent='文件已更改，请点击「上传并预检」重新检查。';
  $('applyBatch').disabled=true;
  $('reviewCurrentBatch').disabled=true;
  $('prepareM6Package').disabled=true;
  $('downloadM6Package').disabled=true;
  $('prepareM6Overlay').disabled=true;
  $('downloadM6Overlay').disabled=true;
  $('inspectM6OCR').disabled=true;
  resetM6OCRReview();
  clearM6CutoutReview();
  $('m6OverlayStatus').textContent='先准备 M6 审核包并在本机采集 OCR 证据。';
  $('m6PackageStatus').textContent='批次尚未完成，不能生成 M6 审核包。';
  $('batchProgress').value=0;
  $('batchRows').replaceChildren();
  $('batchPlanSummary').textContent='';
  $('batchStatus').textContent='文件已更改，请重新预检。';
}
function renderBatchState(state){
  if(!state || state.batch_id!==currentBatchId)return;
  if(m6CutoutReviewBatch && m6CutoutReviewBatch!==state.batch_id)
    clearM6CutoutReview();
  localStorage.setItem(BATCH_STORAGE_KEY,state.batch_id);
  lastBatchStatus=state.status;
  $('batchResults').hidden=state.status!=='COMPLETED';
  $('applyBatch').hidden=!['READY','RUNNING','FAILED','INTERRUPTED'].includes(state.status);
  $('preflightBatch').classList.add('secondary');
  setBatchStage(state.status==='COMPLETED'?3:2);
  $('batchNextAction').textContent=({
    READY:'清单检查通过。下一步点击「生成海报」，开始处理这 '+state.total+' 个商品。',
    RUNNING:'正在生成。你可以切换工作区；刷新页面后也能继续查看进度。',
    COMPLETED:'已完成 '+state.total+' 个商品。接下来逐张审核，或准备并下载离线审核包。',
    FAILED:'生成已中断，已完成的商品会保留。点击「继续批次」处理剩余商品。',
    INTERRUPTED:'服务曾中断。点击「继续批次」，会复用已保存的结果。'
  })[state.status]||'请查看批次状态后继续。';
  $('batchManifest').disabled=state.status==='RUNNING';
  $('batchImages').disabled=state.status==='RUNNING';
  const total=Number(state.total)||0;
  const processed=Math.min(total,Number(state.processed)||0);
  $('batchProgress').value=total?Math.round(processed*100/total):0;
  const current=state.current_sku?' · 当前 SKU：'+state.current_sku:'';
  const error=state.error?' · '+state.error:'';
  $('batchStatus').textContent=batchStatusLabel(state.status)+' · '+processed+'/'+total+current+error;
  $('batchStatus').className='tip '+(state.status==='COMPLETED'?'good':state.status==='FAILED'?'bad':'');
  $('batchPlanSummary').textContent=state.plan
    ? '共 '+state.plan.total_skus+' 个商品 · '+state.plan.unique_source_files+
      ' 张不同素材 · '+(state.plan.no_paid_model?'使用免费本地背景':'请核对背景模式')+
      (state.plan.layout_source?' · 方形版式源 v'+state.plan.layout_source.version+
        '（'+state.plan.layout_source.poster_id.slice(0,8)+'）':'')
    : '';
  $('applyBatch').disabled=!['READY','FAILED','INTERRUPTED'].includes(state.status);
  $('reviewCurrentBatch').disabled=state.status!=='COMPLETED';
  $('prepareM6Package').disabled=state.status!=='COMPLETED';
  $('downloadM6Package').disabled=true;
  $('prepareM6Overlay').disabled=true;
  $('downloadM6Overlay').disabled=true;
  $('inspectM6OCR').disabled=true;
  if(m6OcrReviewBatch && m6OcrReviewBatch!==state.batch_id)resetM6OCRReview();
  if(state.status==='COMPLETED')refreshM6PackageStatus(state.batch_id);
  else{
    resetM6OCRReview();
    $('m6PackageStatus').textContent='批次尚未完成，不能生成 M6 审核包。';
  }
  $('applyBatch').textContent=(state.status==='FAILED'||state.status==='INTERRUPTED')
    ? '继续批次' : state.status==='COMPLETED' ? '批次已完成' : '生成海报（本地背景）';
  $('preflightBatch').disabled=state.status==='RUNNING';
  const host=$('batchRows');
  host.replaceChildren();
  for(const item of (state.rows||[])){
    const row=document.createElement('div');
    row.className='execution-row';
    row.dataset.sku=item.sku_id.toLowerCase();
    if(item.poster_id){
      const thumbnail=document.createElement('img');
      thumbnail.className='batch-thumb';thumbnail.alt='';thumbnail.loading='lazy';
      thumbnail.src='/api/posters/'+item.poster_id+'/versions/'+item.version+'/image';
      row.appendChild(thumbnail);
    }
    const summary=document.createElement('span');
    summary.className='summary';
    summary.textContent='SKU '+item.sku_id+' · '+batchStatusLabel(item.status)+
      (item.poster_id?' · 海报 '+item.poster_id.slice(0,8)+'…':'');
    row.appendChild(summary);
    if(item.poster_id){
      const open=document.createElement('button');
      open.type='button';open.className='secondary';open.textContent='打开海报';
      open.addEventListener('click',()=>restoreExecutionPoster(item.poster_id));
      row.appendChild(open);
    }
    host.appendChild(row);
  }
  filterBatchRows();
  if(batchPollTimer){clearTimeout(batchPollTimer);batchPollTimer=null;}
  if(state.status==='RUNNING')scheduleBatchPoll(state.batch_id);
}
function scheduleBatchPoll(batchId){
  if(batchPollTimer)clearTimeout(batchPollTimer);
  const generation=++batchPollGeneration;
  batchPollTimer=setTimeout(async()=>{
    if(generation!==batchPollGeneration||currentBatchId!==batchId)return;
    await refreshBatchState(batchId);
  },1500);
}
async function refreshBatchState(batchId){
  try{
    const response=await fetch('/api/batches/'+encodeURIComponent(batchId));
    const state=await response.json();
    if(!response.ok)throw Error(errorText(state));
    if(currentBatchId===batchId)renderBatchState(state);
  }catch(err){
    if(currentBatchId===batchId){
      $('batchStatus').textContent='读取批次状态失败：'+err.message;
      $('batchStatus').className='tip bad';
      if(lastBatchStatus==='RUNNING')scheduleBatchPoll(batchId);
    }
  }
}
function populateSavedLayoutPicker(data){
  const picker=$('batchTemplateVersion');
  const previous=picker.value;
  picker.replaceChildren();
  for(const version of [...data.versions].reverse()){
    const item=document.createElement('option');
    item.value=String(version);
    item.textContent='v'+version+(version===data.version?'（最新）':'（历史）');
    picker.appendChild(item);
  }
  picker.value=data.versions.includes(Number(previous)) ? previous : String(data.version);
  picker.disabled=!$('batchUseSavedLayout').checked;
}
$('batchUseSavedLayout').addEventListener('change',()=>{
  if($('batchUseSavedLayout').checked && !current){
    $('batchUseSavedLayout').checked=false;
    $('batchTemplateStatus').textContent='没有已保存海报。请先创建或打开一张模板来源海报。';
    return;
  }
  $('batchTemplateVersion').disabled=!$('batchUseSavedLayout').checked;
  $('batchTemplateStatus').textContent=$('batchUseSavedLayout').checked
    ? '将复用海报 '+current.poster_id.slice(0,8)+' 的所选历史版本，预检时冻结版式，不修改 SKU 业务字段。'
    : '当前不复用已有海报版式，按 CSV 中的版式字段或默认经典版式导入。';
  resetBatchForNewFiles();
});
$('batchTemplateVersion').addEventListener('change',resetBatchForNewFiles);
function updateBatchFileSummary(){
  const manifest=$('batchManifest').files[0];
  const images=Array.from($('batchImages').files||[]);
  $('batchFileSummary').textContent=(manifest?manifest.name:'未选择 CSV')+' · 已选择 '+images.length+' 张图片';
  $('batchManifest').removeAttribute('aria-invalid');
  $('batchImages').removeAttribute('aria-invalid');
}
$('batchManifest').addEventListener('change',resetBatchForNewFiles);
$('batchImages').addEventListener('change',resetBatchForNewFiles);

let campaignPresetsById=new Map();
// Explicit, non-destructive campaign copy preset: fills only blank fields
// and returns a fresh CSV. Source file, paid settings and batch state stay unchanged.
async function loadCampaignPresets(){
  const picker=$('campaignPreset');
  const action=$('applyCampaignPreset');
  try{
    const response=await fetch('/api/campaign-presets',{cache:'no-store'});
    const data=await response.json();
    if(!response.ok||!Array.isArray(data.items))throw Error(errorText(data));
    campaignPresetsById=new Map(data.items.map(item=>[item.id,item]));
    for(const id of ['campaignPreset','createCampaignPreset']){
      const select=$(id);
      select.replaceChildren();
      const hint=document.createElement('option');hint.value='';hint.textContent='选择活动预设';select.appendChild(hint);
      for(const preset of data.items){
        const option=document.createElement('option');
        option.value=preset.id;
        option.textContent=preset.name+' · '+preset.style;
        select.appendChild(option);
      }
      select.disabled=false;
    }
    action.disabled=true;
  }catch(err){
    picker.replaceChildren();
    const failed=document.createElement('option');failed.textContent='预设无法载入';picker.appendChild(failed);
    picker.disabled=true;action.disabled=true;
    $('campaignPresetStatus').textContent='载入活动预设失败：'+err.message;
    $('campaignPresetStatus').className='tip bad';
  }
}
$('campaignPreset').addEventListener('change',()=>{
  $('applyCampaignPreset').disabled=!$('campaignPreset').value;
});
$('applyCampaignPreset').addEventListener('click',async()=>{
  const manifest=$('batchManifest').files[0],preset=$('campaignPreset').value;
  const status=$('campaignPresetStatus'),button=$('applyCampaignPreset');
  if(!manifest||!preset){
    status.textContent='请先选择 SKU 清单和活动预设。';
    status.className='tip bad';return;
  }
  button.disabled=true;button.setAttribute('aria-busy','true');
  status.className='tip';status.textContent='正在本机检查并补全清单；不会创建海报。';
  const form=new FormData();
  form.append('manifest',manifest,manifest.name);
  form.append('template_id',preset);
  try{
    const response=await fetch('/api/campaign-presets/apply-manifest',{method:'POST',body:form});
    if(!response.ok){
      const failure=await response.json();
      throw Error(errorText(failure));
    }
    const blob=await response.blob();
    const objectUrl=URL.createObjectURL(blob);
    const download=document.createElement('a');
    download.href=objectUrl;
    download.download='campaign-manifest.csv';
    document.body.appendChild(download);
    download.click();
    download.remove();
    setTimeout(()=>URL.revokeObjectURL(objectUrl),1000);
    status.textContent='已下载补全清单（'+(response.headers.get('x-template-filled-rows')||'?')+
      ' 行需要补全）。原 CSV 未修改。请重新选择下载的 CSV，上传图片并预检。';
    status.className='tip';
  }catch(err){
    status.textContent='预设应用失败：'+err.message;
    status.className='tip bad';
  }finally{
    button.disabled=!$('campaignPreset').value;
    button.removeAttribute('aria-busy');
  }
});

$('createCampaignPreset').addEventListener('change',()=>{
  $('applySingleCampaignPreset').disabled=!$('createCampaignPreset').value;
});
$('applySingleCampaignPreset').addEventListener('click',()=>{
  const status=$('createCampaignPresetStatus');
  if(activeReviewRunId){
    status.textContent='当前存在 Brief 审核任务。请先点击「清除任务 / 手动填写」，再套用活动预设。';
    status.className='tip bad';return;
  }
  const preset=campaignPresetsById.get($('createCampaignPreset').value);
  if(!preset)return;
  field('subtitle').value=preset.subtitle;
  field('cta').value=preset.cta;
  field('style').value=preset.style;
  syncStylePresetPicker();
  status.className='tip';
  status.textContent='已套用「'+preset.name+'」的副标题、CTA 和配色；价格与主标题未修改。请人工检查后再生成。';
});
loadCampaignPresets();

$('preflightBatch').addEventListener('click',async()=>{
  const manifest=$('batchManifest').files[0];
  const images=Array.from($('batchImages').files||[]);
  if(!manifest||!images.length){
    $('batchManifest').setAttribute('aria-invalid',String(!manifest));
    $('batchImages').setAttribute('aria-invalid',String(!images.length));
    (!manifest?$('batchManifest'):$('batchImages')).focus();
    $('batchStatus').textContent='请同时选择 CSV 和该清单引用的全部图片。';
    $('batchStatus').className='tip bad';
    return;
  }
  const body=new FormData();
  body.append('manifest',manifest,manifest.name);
  for(const file of images)body.append('images',file,file.name);
  if($('batchUseSavedLayout').checked){
    const version=Number($('batchTemplateVersion').value);
    if(!current || !current.versions.includes(version)){
      $('batchStatus').textContent='模板来源或历史版本已失效，请重新打开来源海报。';
      $('batchStatus').className='tip bad';
      return;
    }
    body.append('layout_poster_id',current.poster_id);
    body.append('layout_version',String(version));
  }
  $('preflightBatch').disabled=true;
  $('applyBatch').disabled=true;
  $('batchManifest').disabled=true;
  $('batchImages').disabled=true;
  setBatchStage(1);
  $('preflightBatch').setAttribute('aria-busy','true');
  $('batchNextAction').textContent='正在检查清单和图片。此步骤不会创建海报。';
  $('batchStatus').textContent='正在上传到本机并校验全部 SKU；此步骤不会创建海报…';
  $('batchStatus').className='tip';
  try{
    const response=await fetch('/api/batches',{method:'POST',body});
    const state=await response.json();
    if(!response.ok)throw Error(errorText(state));
    batchHistorySelectionRequest+=1;
    currentBatchId=state.batch_id;
    renderBatchState(state);
    loadBatchHistory();
  }catch(err){
    setBatchStage(0);
    $('batchNextAction').textContent='请按下方提示修正文件，再重新预检。';
    $('batchStatus').textContent='批次预检失败：'+err.message;
    $('batchStatus').className='tip bad';
  }finally{
    $('preflightBatch').disabled=false;
    $('preflightBatch').removeAttribute('aria-busy');
    $('batchManifest').disabled=false;
    $('batchImages').disabled=false;
  }
});
$('applyBatch').addEventListener('click',async()=>{
  if(!currentBatchId)return;
  const batchId=currentBatchId;
  $('applyBatch').disabled=true;
  $('batchStatus').textContent='正在启动本地批次…';
  try{
    const response=await fetch('/api/batches/'+encodeURIComponent(batchId)+'/apply',{method:'POST'});
    const state=await response.json();
    if(!response.ok)throw Error(errorText(state));
    if(currentBatchId===batchId)renderBatchState(state);
    await refreshBatchState(batchId);
  }catch(err){
    $('batchStatus').textContent='无法启动批次：'+err.message;
    $('batchStatus').className='tip bad';
    $('applyBatch').disabled=false;
  }
});

let m6CutoutReviewState=null;
let m6CutoutReviewBatch=null;
let m6CutoutReviewRequest=0;
function clearM6CutoutReview(){
  m6CutoutReviewRequest++;
  m6CutoutReviewState=null;
  m6CutoutReviewBatch=null;
  $('m6CutoutReviewPanel').hidden=true;
  $('openM6CutoutReview').disabled=true;
  $('m6CutoutSave').disabled=true;
  $('m6CutoutComparison').removeAttribute('src');
  $('m6CutoutSku').replaceChildren();
  $('m6CutoutStatus').textContent='';
  $('m6CutoutMachineRisks').textContent='';
}
function drawM6CutoutReview(){
  const state=m6CutoutReviewState;
  if(!state||currentBatchId!==m6CutoutReviewBatch)return;
  const sku=$('m6CutoutSku').value;
  const row=state.rows.find(x=>x.sku_id===sku);
  const active=row?.quality_state==='REVIEW_REQUIRED';
  $('m6CutoutSave').disabled=!active;
  $('m6CutoutComparison').removeAttribute('src');
  if(active&&row.comparison_url){
    $('m6CutoutComparison').src=row.comparison_url;
  }
  $('m6CutoutMachineRisks').textContent=active
    ? '机器风险提示：'+(row.machine_risks||'未触发规则')+
      ' · 主体覆盖率 '+row.foreground_fraction+
      ' · 半透明比例 '+row.soft_alpha_fraction
    : '此 SKU 未采用有效的透明抠图候选，当前无须抠图人工评价。';
  for(const [id,key] of [
    ['m6CutoutSubject','subject_integrity_review'],
    ['m6CutoutEdge','edge_artifact_review'],
    ['m6CutoutBrand','brand_mark_review'],
    ['m6CutoutDecision','human_accept']
  ]){
    const value=row?.[key]||'';
    $(id).value=['PASS','FAIL'].includes(value)?value:'';
    $(id).disabled=!active;
  }
  $('m6CutoutReviewer').value=row?.reviewer_alias||'';
  $('m6CutoutNotes').value=row?.reviewer_notes||'';
  $('m6CutoutReviewer').disabled=!active;
  $('m6CutoutNotes').disabled=!active;
  $('m6CutoutStatus').textContent=sku+' · '+(row?.human_accept||'UNVERIFIED')+
    ' · 已核对该批次的冻结来源文件。已记录的操作员评价仅供后续独立验收。';
  $('m6CutoutReviewPanel').hidden=false;
}
async function loadM6CutoutReview(batchId,sku=null){
  const sequence=++m6CutoutReviewRequest;
  $('m6CutoutStatus').textContent='正在校验本地 M6 抠图来源与 SHA-256…';
  $('m6CutoutSave').disabled=true;
  try{
    const response=await fetch('/api/batches/'+encodeURIComponent(batchId)+'/m6-cutout-review');
    const data=await response.json();
    if(sequence!==m6CutoutReviewRequest||currentBatchId!==batchId)return;
    if(!response.ok)throw Error(errorText(data));
    if(!Array.isArray(data.rows)||!data.rows.length)throw Error('没有有效的 SKU 审核记录');
    m6CutoutReviewState=data;
    m6CutoutReviewBatch=batchId;
    const picker=$('m6CutoutSku');
    const prior=sku||picker.value;
    picker.replaceChildren();
    for(const row of data.rows){
      const opt=document.createElement('option');
      opt.value=row.sku_id;opt.textContent=row.sku_id+
        ' · '+(row.human_accept||'UNVERIFIED');picker.appendChild(opt);
    }
    picker.value=data.rows.some(x=>x.sku_id===prior)?prior:
      (data.rows.find(x=>x.quality_state==='REVIEW_REQUIRED')||data.rows[0]).sku_id;
    drawM6CutoutReview();
  }catch(err){
    if(sequence!==m6CutoutReviewRequest||currentBatchId!==batchId)return;
    m6CutoutReviewState=null;
    $('m6CutoutSave').disabled=true;
    $('m6CutoutComparison').removeAttribute('src');
    $('m6CutoutReviewPanel').hidden=false;
    $('m6CutoutStatus').textContent='抠图审核来源校验失败：'+err.message+
      '。如其他页面修改了审核表，请点击重新加载。';
  }
}
$('openM6CutoutReview').addEventListener('click',()=>{
  if(currentBatchId&&lastBatchStatus==='COMPLETED')
    loadM6CutoutReview(currentBatchId);
});
$('m6CutoutReload').addEventListener('click',()=>{
  if(currentBatchId)loadM6CutoutReview(currentBatchId);
});
$('m6CutoutSku').addEventListener('change',drawM6CutoutReview);
$('m6CutoutSave').addEventListener('click',async()=>{
  const state=m6CutoutReviewState;
  if(!state||state.batch_id!==currentBatchId)return;
  const batchId=state.batch_id,sku=$('m6CutoutSku').value;
  const params={
    expected_csv_sha256:state.csv_sha256,sku_id:sku,
    subject_integrity_review:$('m6CutoutSubject').value,
    edge_artifact_review:$('m6CutoutEdge').value,
    brand_mark_review:$('m6CutoutBrand').value,
    human_accept:$('m6CutoutDecision').value,
    reviewer_alias:$('m6CutoutReviewer').value,
    reviewer_notes:$('m6CutoutNotes').value,
  };
  $('m6CutoutSave').disabled=true;
  try{
    if(['subject_integrity_review','edge_artifact_review','brand_mark_review',
         'human_accept'].some(key=>!['PASS','FAIL'].includes(params[key])))
      throw Error('请完成主体、边缘、品牌和总评四项审核');
    const response=await fetch('/api/batches/'+encodeURIComponent(batchId)+'/m6-cutout-review',{
      method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(params)
    });
    const result=await response.json();
    if(currentBatchId!==batchId)return;
    if(!response.ok)throw Error(errorText(result));
    await loadM6CutoutReview(batchId,sku);
    if(currentBatchId===batchId)$('m6CutoutStatus').textContent=
      '人工审核已原子保存，原始 CSV 版本已留存。当前状态：'+result.status+
      '。模型真实性与商品质量由独立验收确认。';
  }catch(err){
    if(currentBatchId!==batchId)return;
    $('m6CutoutStatus').textContent='保存失败：'+err.message+
      '。请核对输入；若审核表已变化，请重新加载。';
    $('m6CutoutSave').disabled=true;
  }
});
async function refreshM6PackageStatus(batchId){
  if(currentBatchId!==batchId)return;
  const label=$('m6PackageStatus');
  label.textContent='正在读取本机 M6 审核包状态…';
  try{
    const response=await fetch('/api/batches/'+encodeURIComponent(batchId)+'/m6-package');
    const result=await response.json();
    if(currentBatchId!==batchId)return;
    if(!response.ok)throw Error(errorText(result));
    label.title='本地审核目录：'+result.relative_directory;
    $('prepareM6Package').disabled=result.prepared;
    $('downloadM6Package').disabled=!result.prepared;
    $('openM6CutoutReview').disabled=!result.prepared;
    if(result.prepared)refreshM6OverlayStatus(batchId);
    else{
      $('captureM6OCR').disabled=true;
      $('m6OCRCaptureStatus').textContent='请先生成 M6 审核包，再检查本机 OCR 模型与运行环境。';
      $('prepareM6Overlay').disabled=true;
      $('downloadM6Overlay').disabled=true;
      $('m6OverlayStatus').textContent='请先生成 M6 审核包并在本机采集 OCR 证据。';
    }
    label.textContent=result.prepared
      ? '离线审核包已准备好，可下载图片与审核表。已有文件会保留，不能重复覆盖。'
      : '已完成 '+result.batch_sku_count+' 个 SKU；可显式准备 '+
        result.expected_review_rows+' 条方形/4:5 待审记录（无 OCR、无付费调用）。';
  }catch(err){
    if(currentBatchId===batchId){
      $('prepareM6Package').disabled=true;
      $('downloadM6Package').disabled=true;
      $('openM6CutoutReview').disabled=true;
      $('captureM6OCR').disabled=true;
      $('m6OCRCaptureStatus').textContent='M6 审核包预检未通过，无法执行 OCR。';
      $('prepareM6Overlay').disabled=true;
      $('downloadM6Overlay').disabled=true;
      label.textContent='M6 审核包预检未通过：'+err.message;
    }
  }
}
$('prepareM6Package').addEventListener('click',async()=>{
  const batchId=currentBatchId;
  if(!batchId||lastBatchStatus!=='COMPLETED')return;
  const button=$('prepareM6Package'),label=$('m6PackageStatus');
  button.disabled=true;
  label.textContent='正在本机离线复制方图并生成 4:5 草稿，原始海报与已审核结果不会被覆盖…';
  try{
    const response=await fetch('/api/batches/'+encodeURIComponent(batchId)+'/m6-package',{
      method:'POST'
    });
    const result=await response.json();
    if(currentBatchId!==batchId)return;
    if(!response.ok)throw Error(errorText(result));
    $('downloadM6Package').disabled=false;
    $('openM6CutoutReview').disabled=false;
    refreshM6OverlayStatus(batchId);
    label.textContent='离线审核包已准备好：'+result.batch_sku_count+' 个商品 · '+result.review_rows_created+' 条待审记录'+
      ' · 缺失输出 '+result.missing_output_count+
      '。先填写授权和人工评审，再运行真实 OCR 与最终证据校验。';
  }catch(err){
    if(currentBatchId===batchId){
      label.textContent='无法生成 M6 审核包：'+err.message;
      // A failed attempt may have left a partial directory. Recheck first;
      // never enable blind retries that could overwrite reviewer work.
      await refreshM6PackageStatus(batchId);
    }
  }
});

$('downloadM6Package').addEventListener('click',()=>{
  const batchId=currentBatchId;
  if(!batchId || lastBatchStatus!=='COMPLETED' ||
     $('downloadM6Package').disabled)return;
  // The server enforces local-only access, exact contents and SHA-256 before
  // it sends ZIP response headers. Browser download never deletes local files.
  const link=document.createElement('a');
  link.href='/api/batches/'+encodeURIComponent(batchId)+'/m6-package/download';
  link.download='m6-review-'+batchId+'.zip';
  document.body.appendChild(link);
  link.click();
  link.remove();
});

let m6OcrReviewBatch=null;
let m6OcrReviewRequest=0;
const m6ReviewFields=[
  ['sku_integrity','SKU 完整性'],['text_legibility','文字可读性'],
  ['title','标题'],['subtitle','副标题'],['price','售价'],
  ['original_price','原价'],['price_position','售价位置'],
  ['original_price_position','原价位置'],['cta','行动按钮'],
  ['composition','构图']
];
const m6ReviewDrafts=new Map();
let m6ReviewSource=null;
let m6ReviewAssessment=null;
let m6ReviewEvidenceReady=false;
function invalidateM6ReviewAssessment(){
  m6ReviewAssessment=null;
  $('m6ReviewDownloadAssessment').disabled=true;
  $('m6ReviewAssessmentStatus').textContent='草稿有变动，需重新核对冻结 CSV、OCR 与图片 SHA-256。仍不构成商品质量验收。';
}
function renderM6ReviewQueue(){
  const host=$('m6ReviewQueue'),summary=$('m6ReviewQueueSummary');
  host.replaceChildren();
  if(!m6ReviewEvidenceReady||!m6ReviewSource||m6ReviewSource.batchId!==currentBatchId){
    summary.textContent='冻结 OCR 证据尚未完成当前核验；待办导航暂不可用。';
    return;
  }
  const ids=[...$('m6OcrSkuPicker').options].map(option=>option.value);
  const variants=[['square','方形'],['feed_4x5_native','原生 4:5']];
  const filter=$('m6ReviewQueueFilter').value;
  let pendingSkus=0,failedSkus=0,declaredComplete=0,shown=0;
  for(const sku of ids){
    const missing=[],failed=[];
    for(const [variant,label] of variants){
      const decision=m6ReviewDrafts.get(sku+'/'+variant);
      if(!decision)missing.push(label);
      else if(decision.overall_accept==='FAIL')failed.push(label);
    }
    if(missing.length)pendingSkus++;
    if(failed.length)failedSkus++;
    if(!missing.length&&!failed.length)declaredComplete++;
    if((filter==='actionable'&&!missing.length&&!failed.length)||
       (filter==='missing'&&!missing.length)||(filter==='failed'&&!failed.length))continue;
    const row=document.createElement('div');
    row.className='execution-row';
    row.setAttribute('role','listitem');
    const button=document.createElement('button');
    button.type='button';
    button.className='secondary';
    button.textContent='查看 '+sku;
    button.setAttribute('aria-label','核对 '+sku+' 的 OCR 与人工审核');
    button.addEventListener('click',()=>{
      const batchId=m6ReviewSource?.batchId;
      if(currentBatchId===batchId&&m6OcrReviewBatch===batchId)
        loadM6OCRReview(batchId,sku);
    });
    const detail=document.createElement('span');
    detail.className='tip';
    const reasons=[];
    if(missing.length)reasons.push('待填草稿：'+missing.join('、'));
    if(failed.length)reasons.push('操作员报告 FAIL：'+failed.join('、'));
    if(!reasons.length)reasons.push('两种版式已有草稿，待人工验收');
    detail.textContent=' · '+reasons.join('；')+' · OCR 记录未获运行真实性证明';
    row.append(button,detail);
    host.appendChild(row);
    shown++;
  }
  summary.textContent='SKU '+ids.length+' 个 · 缺草稿 '+pendingSkus+
    ' 个 · 操作员报告 FAIL '+failedSkus+' 个 · 无待办草稿 '+declaredComplete+
    ' 个 · 当前显示 '+shown+' 个。仅为当前页面草稿状态；请下载侧车以保存。';
  if(!shown){
    const empty=document.createElement('p');
    empty.className='tip';
    empty.textContent='当前筛选无待办 SKU；不代表海报已通过质量验收。';
    host.appendChild(empty);
  }
}
$('m6ReviewQueueFilter').addEventListener('change',renderM6ReviewQueue);
function updateM6ReviewProgress(){
  const picker=$('m6OcrSkuPicker');
  const ids=[...picker.options].map(item=>item.value);
  const expected=ids.length*2;
  const recorded=ids.reduce((count,sku)=>count+
    ['square','feed_4x5_native'].filter(variant=>m6ReviewDrafts.has(sku+'/'+variant)).length,0);
  const pending=expected-recorded;
  $('m6ReviewDraftProgress').textContent='页面内暂存 '+recorded+'/'+expected+
    ' 个版式审核 · 尚缺 '+pending+' 条。这里只表示草稿数量，尚未经独立验收。';
  $('m6ReviewAssess').disabled=!m6ReviewEvidenceReady||!m6ReviewSource||recorded===0;
  $('m6ReviewNextMissing').disabled=!m6ReviewEvidenceReady||!m6ReviewSource||pending===0;
  $('m6ReviewDownloadWorksheet').disabled=!m6ReviewEvidenceReady||!m6ReviewSource||recorded===0;
  renderM6ReviewQueue();
}
function resetM6OCRReview(){
  m6OcrReviewRequest+=1;
  m6OcrReviewBatch=null;
  m6ReviewDrafts.clear();
  m6ReviewSource=null;
  m6ReviewAssessment=null;
  m6ReviewEvidenceReady=false;
  $('m6ReviewAssess').disabled=true;
  $('m6ReviewNextMissing').disabled=true;
  $('m6ReviewDownloadAssessment').disabled=true;
  $('m6ReviewDownloadWorksheet').disabled=true;
  $('m6ReviewDraftProgress').textContent='尚无已保存的人工审核草稿。';
  $('m6ReviewQueueFilter').value='actionable';
  $('m6ReviewQueueSummary').textContent='加载 OCR 证据后可查看 SKU 待办。';
  $('m6ReviewQueue').replaceChildren();
  $('m6ReviewAssessmentStatus').textContent='核对结果仅表明操作员声明和本地摘要一致，不是商品质量验收。';
  $('m6ReviewImportFile').value='';
  $('m6ReviewImportSidecar').disabled=true;
  $('m6ReviewSaveSku').disabled=true;
  $('m6ReviewExportSidecar').disabled=true;
  $('m6ReviewSidecarStatus').textContent='暂存仅在当前页面内有效；请显式下载 JSON 保存，不会改写冻结 CSV。';
  $('m6OcrReviewPanel').hidden=true;
  $('m6OcrSkuPicker').replaceChildren();
  $('m6OcrSkuPicker').disabled=true;
  $('m6OcrReviewStatus').textContent='';
  $('m6OcrReviewRows').replaceChildren();
}
function drawM6OCRReview(data,batchId){
  const picker=$('m6OcrSkuPicker');
  if(!Array.isArray(data.sku_ids)||data.sku_ids.length<1||data.sku_ids.length>100||
     !Array.isArray(data.variants)||data.variants.length!==2)
    throw Error('服务器返回的逐 SKU 证据结构无效');
  picker.replaceChildren();
  for(const sku of data.sku_ids){
    const option=document.createElement('option');
    option.value=sku;option.textContent=sku;picker.appendChild(option);
  }
  if(!m6ReviewSource ||
     m6ReviewSource.batchId!==batchId ||
     m6ReviewSource.reviews_csv_sha256!==data.reviews_csv_sha256 ||
     m6ReviewSource.ocr_capture_sha256!==data.ocr_capture_sha256){
    m6ReviewDrafts.clear();
    invalidateM6ReviewAssessment();
    m6ReviewSource={
      batchId:batchId,
      reviews_csv_sha256:data.reviews_csv_sha256,
      ocr_capture_sha256:data.ocr_capture_sha256
    };
    $('m6ReviewExportSidecar').disabled=true;
  }
  picker.value=data.selected_sku_id;
  picker.disabled=false;
  $('m6ReviewImportSidecar').disabled=!$('m6ReviewImportFile').files.length;
  $('m6ReviewSaveSku').disabled=false;
  $('m6ReviewExportSidecar').disabled=m6ReviewDrafts.size===0;
  $('m6OcrReviewPanel').hidden=false;
  $('m6OcrReviewStatus').textContent='已核对冻结证据：'+data.sku_ids.length+
    ' 个 SKU · 当前 '+data.selected_sku_id+' · OCR 仅供人工复核，不证明模型推理或质量 PASS。';
  const host=$('m6OcrReviewRows');
  host.replaceChildren();
  for(const variant of data.variants){
    const card=document.createElement('section');
    card.className='mt';
    const heading=document.createElement('h4');
    heading.textContent=variant.variant_id==='square'?'方形 1024×1024':'原生 4:5 1080×1350';
    card.appendChild(heading);
    // The browser never accepts caller-provided paths: only a server-verified
    // SKU/variant endpoint can return the exact SHA-pinned original PNG.
    const comparison=document.createElement('div');
    comparison.style.display='grid';
    comparison.style.gridTemplateColumns='repeat(auto-fit, minmax(min(100%, 280px), 1fr))';
    comparison.style.gap='16px';
    const figure=document.createElement('figure');
    figure.style.margin='0';
    const picture=document.createElement('img');
    picture.className='m6-ocr-evidence-image';
    picture.alt='待人工核对的冻结海报：'+data.selected_sku_id+' · '+heading.textContent;
    picture.loading='lazy';
    picture.decoding='async';
    picture.style.width='100%';
    picture.style.maxWidth='420px';
    picture.style.height='auto';
    picture.style.display='block';
    picture.src='/api/batches/'+encodeURIComponent(batchId)+'/m6-ocr-review/'+
      encodeURIComponent(data.selected_sku_id)+'/image/'+encodeURIComponent(variant.variant_id);
    picture.addEventListener('error',()=>{
      const warning=document.createElement('p');
      warning.className='tip';
      warning.textContent='图片验证失败或冻结证据已过期，请重新核对审核包和 OCR 记录。';
      picture.replaceWith(warning);
    });
    figure.appendChild(picture);
    const imageInfo=document.createElement('figcaption');
    imageInfo.className='tip';
    imageInfo.textContent='冻结原图 · SHA-256 '+variant.image_sha256.slice(0,12)+
      '… · 不代表已通过质量验收';
    figure.appendChild(imageInfo);
    comparison.appendChild(figure);

    const detail=document.createElement('div');
    const review=document.createElement('p');
    const labels=variant.review_labels||{};
    review.textContent='人工审核 · 标题 '+(labels.title||'UNVERIFIED')+
      ' · 售价 '+(labels.price||'UNVERIFIED')+
      ' · 价格位置 '+(labels.price_position||'UNVERIFIED')+
      ' · 总体 '+(labels.overall_accept||'UNVERIFIED');
    detail.appendChild(review);
    const metadata=document.createElement('p');
    metadata.className='tip';
    metadata.textContent='识别行 '+variant.recognized_line_count+
      ' · 无定位 '+variant.unlocated_line_count+
      ' · 仅展示前 '+variant.preview_line_count+
      ' 行；剩余 '+variant.lines_truncated_count+' 行请查看原始 OCR JSON。';
    detail.appendChild(metadata);
    const list=document.createElement('ol');
    for(const line of variant.lines||[]){
      const item=document.createElement('li');
      const lineNode=document.createElement('span');
      lineNode.textContent=line.text;
      item.appendChild(lineNode);
      const details=document.createElement('span');
      details.className='tip';
      details.textContent=' · 置信度 '+line.confidence.toFixed(2)+
        (Array.isArray(line.bbox)?' · 坐标 '+line.bbox.join(','):' · 坐标未知');
      item.appendChild(details);
      list.appendChild(item);
    }
    detail.appendChild(list);
    comparison.appendChild(detail);
    card.appendChild(comparison);
    card.dataset.m6Variant=variant.variant_id;
    const key=data.selected_sku_id+'/'+variant.variant_id;
    const saved=m6ReviewDrafts.get(key);
    const worksheet=document.createElement('fieldset');
    const legend=document.createElement('legend');
    legend.textContent='人工审核草稿（不会自动判定）';
    worksheet.appendChild(legend);
    const grid=document.createElement('div');
    grid.style.display='grid';
    grid.style.gridTemplateColumns='repeat(auto-fit, minmax(min(100%, 200px), 1fr))';
    grid.style.gap='10px';
    for(const [field,labelText] of m6ReviewFields){
      const label=document.createElement('label');
      label.textContent=labelText;
      const select=document.createElement('select');
      select.dataset.m6Check=field;
      for(const value of ['UNVERIFIED','PASS','FAIL','N/A']){
        const option=document.createElement('option');
        option.value=value;option.textContent=value;select.appendChild(option);
      }
      select.value=saved?.checks[field]||variant.review_labels?.[field]||'UNVERIFIED';
      label.appendChild(select);
      grid.appendChild(label);
    }
    worksheet.appendChild(grid);
    const reviewer=document.createElement('label');
    reviewer.textContent='审核员代号';
    const alias=document.createElement('input');
    alias.dataset.m6Alias='true';
    alias.maxLength=64;
    alias.value=saved?.reviewer_alias||variant.review_labels?.reviewer_alias||'';
    reviewer.appendChild(alias);
    worksheet.appendChild(reviewer);
    const overall=document.createElement('label');
    overall.textContent='人工总体标签';
    const overallSelect=document.createElement('select');
    overallSelect.dataset.m6Overall='true';
    for(const value of ['UNVERIFIED','PASS','FAIL']){
      const option=document.createElement('option');
      option.value=value;option.textContent=value;overallSelect.appendChild(option);
    }
    overallSelect.value=saved?.overall_accept||variant.review_labels?.overall_accept||'UNVERIFIED';
    overall.appendChild(overallSelect);
    worksheet.appendChild(overall);
    const reason=document.createElement('label');
    reason.textContent='失败原因（FAIL 必填）';
    const reasonField=document.createElement('input');
    reasonField.dataset.m6Reason='true';
    reasonField.maxLength=500;
    reasonField.value=saved?.overall_reason||variant.review_labels?.overall_reason||'';
    reason.appendChild(reasonField);
    worksheet.appendChild(reason);
    card.appendChild(worksheet);
    host.appendChild(card);
  }
  m6ReviewEvidenceReady=true;
  updateM6ReviewProgress();
}
async function loadM6OCRReview(batchId,skuId=null){
  const id=++m6OcrReviewRequest;
  m6ReviewEvidenceReady=false;
  renderM6ReviewQueue();
  $('m6ReviewNextMissing').disabled=true;
  $('m6ReviewAssess').disabled=true;
  $('m6ReviewDownloadWorksheet').disabled=true;
  $('m6ReviewExportSidecar').disabled=true;
  $('m6ReviewImportSidecar').disabled=true;
  $('m6ReviewSaveSku').disabled=true;
  $('m6ReviewDownloadAssessment').disabled=true;
  $('inspectM6OCR').disabled=true;
  $('m6OcrReviewStatus').textContent='正在核对该 SKU 的冻结审核表、OCR 与图片摘要…';
  try{
    const path='/api/batches/'+encodeURIComponent(batchId)+'/m6-ocr-review'+
      (skuId?'?sku_id='+encodeURIComponent(skuId):'');
    const response=await fetch(path);
    const data=await response.json();
    if(currentBatchId!==batchId||id!==m6OcrReviewRequest)return;
    if(!response.ok)throw Error(errorText(data));
    drawM6OCRReview(data,batchId);
    m6OcrReviewBatch=batchId;
  }catch(err){
    if(currentBatchId===batchId&&id===m6OcrReviewRequest){
      m6ReviewEvidenceReady=false;
      renderM6ReviewQueue();
      m6ReviewAssessment=null;
      $('m6ReviewSaveSku').disabled=true;
      $('m6ReviewNextMissing').disabled=true;
      $('m6ReviewAssess').disabled=true;
      $('m6ReviewDownloadWorksheet').disabled=true;
      $('m6ReviewExportSidecar').disabled=true;
      $('m6ReviewImportSidecar').disabled=true;
      $('m6ReviewDownloadAssessment').disabled=true;
      $('m6OcrReviewPanel').hidden=false;
      $('m6OcrReviewStatus').textContent='无法核对 OCR 证据：'+err.message+'。原草稿仍暂存在内存中，请修复证据后重新加载，勿将旧草稿视为验收。';
      $('m6OcrReviewRows').replaceChildren();
    }
  }finally{
    if(currentBatchId===batchId&&id===m6OcrReviewRequest)
      $('inspectM6OCR').disabled=lastBatchStatus!=='COMPLETED';
  }
}
$('m6ReviewImportFile').addEventListener('change',()=>{
  $('m6ReviewImportSidecar').disabled=!(
    m6ReviewEvidenceReady&&m6ReviewSource&&currentBatchId===m6ReviewSource.batchId&&
    $('m6ReviewImportFile').files.length
  );
});
$('m6ReviewImportSidecar').addEventListener('click',async()=>{
  const source=m6ReviewSource,batchId=currentBatchId;
  const input=$('m6ReviewImportFile'),file=input.files?.[0];
  if(!file||!m6ReviewEvidenceReady||!source||source.batchId!==batchId||batchId!==m6OcrReviewBatch)return;
  const button=$('m6ReviewImportSidecar');
  button.disabled=true;
  $('m6ReviewSidecarStatus').textContent='正在重新校验侧车与冻结 CSV、OCR 和图片哈希…';
  try{
    if(file.size===0||file.size>256*1024)
      throw Error('JSON 文件为空或超过 256 KiB 限制');
    const raw=await file.text();
    if(currentBatchId!==batchId||m6ReviewSource!==source)return;
    const response=await fetch('/api/batches/'+encodeURIComponent(batchId)+
      '/m6-review-sidecar/restore',{
        method:'POST',headers:{'Content-Type':'application/json'},body:raw
      });
    const restored=await response.json();
    if(currentBatchId!==batchId||m6ReviewSource!==source)return;
    if(!response.ok)throw Error(errorText(restored));
    if(restored.status!=='OPERATOR_ASSERTED_NOT_ATTESTED'||
       restored.reviews_csv_sha256!==source.reviews_csv_sha256||
       restored.ocr_capture_sha256!==source.ocr_capture_sha256)
      throw Error('审核侧车身份与当前批次不一致');
    const draft=new Map();
    for(const row of restored.decisions||[]){
      const key=row.sku_id+'/'+row.variant_id;
      if(draft.has(key))throw Error('导入文件包含重复审核记录');
      draft.set(key,{
        sku_id:row.sku_id,variant_id:row.variant_id,
        reviewer_alias:row.reviewer_alias,checks:row.checks,
        overall_accept:row.manual_overall_label,overall_reason:row.overall_reason
      });
    }
    if(draft.size!==restored.decision_count||draft.size===0)
      throw Error('审核侧车的记录数量不一致');
    // Commit atomically AFTER all server-side validation and local shape checks.
    m6ReviewDrafts.clear();
    for(const [key,row] of draft)m6ReviewDrafts.set(key,row);
    invalidateM6ReviewAssessment();
    updateM6ReviewProgress();
    $('m6ReviewExportSidecar').disabled=false;
    input.value='';
    $('m6ReviewSidecarStatus').textContent='已恢复 '+draft.size+
      ' 条已核对草稿。它们仍是操作员声明，不代表人工验收或质量 PASS。';
    await loadM6OCRReview(batchId,$('m6OcrSkuPicker').value);
  }catch(err){
    if(currentBatchId===batchId&&m6ReviewSource===source)
      $('m6ReviewSidecarStatus').textContent='恢复失败，已保留当前草稿：'+err.message;
  }finally{
    if(currentBatchId===batchId&&m6ReviewSource===source)
      button.disabled=!m6ReviewEvidenceReady||!input.files.length;
  }
});

function collectM6ReviewCard(card,skuId){
  const checks={};
  for(const input of card.querySelectorAll('select[data-m6-check]')){
    if(!['PASS','FAIL','N/A'].includes(input.value))
      throw Error('请填写全部 10 项检查标签，不能保留 UNVERIFIED');
    checks[input.dataset.m6Check]=input.value;
  }
  if(Object.keys(checks).length!==m6ReviewFields.length)throw Error('检查字段缺失');
  const reviewer_alias=card.querySelector('input[data-m6-alias]').value.trim();
  const overall_accept=card.querySelector('select[data-m6-overall]').value;
  const overall_reason=card.querySelector('input[data-m6-reason]').value.trim();
  if(!/^[A-Za-z0-9_.-]{2,64}$/.test(reviewer_alias))
    throw Error('请填写 2–64 字符的英文/数字审核员代号');
  if(!['PASS','FAIL'].includes(overall_accept))throw Error('需要明确的 PASS 或 FAIL 总体标签');
  if(overall_accept==='PASS'&&Object.values(checks).includes('FAIL'))
    throw Error('有失败项目时不能将总体标签设为 PASS');
  if(overall_accept==='FAIL'&&!overall_reason)throw Error('FAIL 必须填写具体原因');
  return {sku_id:skuId,variant_id:card.dataset.m6Variant,reviewer_alias,
          checks,overall_accept,overall_reason};
}
$('m6ReviewSaveSku').addEventListener('click',()=>{
  if(currentBatchId!==m6OcrReviewBatch||!m6ReviewSource)return;
  try{
    const skuId=$('m6OcrSkuPicker').value;
    const cards=[...$('m6OcrReviewRows').querySelectorAll('section[data-m6-variant]')];
    if(cards.length!==2)throw Error('需要两种版式的完整审核');
    // Validate both before mutating in-memory drafts.
    const decisions=cards.map(card=>collectM6ReviewCard(card,skuId));
    for(const decision of decisions)
      m6ReviewDrafts.set(decision.sku_id+'/'+decision.variant_id,decision);
    invalidateM6ReviewAssessment();
    updateM6ReviewProgress();
    $('m6ReviewExportSidecar').disabled=false;
    $('m6ReviewSidecarStatus').textContent='已在页面内暂存 '+m6ReviewDrafts.size+
      ' 条人工标签，尚未写入任何文件。请点击下载审核侧车 JSON。';
  }catch(err){
    $('m6ReviewSidecarStatus').textContent='暂存失败：'+err.message;
  }
});
$('m6ReviewNextMissing').addEventListener('click',()=>{
  const source=m6ReviewSource,batchId=currentBatchId;
  if(!source||source.batchId!==batchId||m6OcrReviewBatch!==batchId)return;
  const picker=$('m6OcrSkuPicker');
  const ids=[...picker.options].map(option=>option.value);
  const start=ids.indexOf(picker.value);
  const ordered=ids.slice(start+1).concat(ids.slice(0,start+1));
  const next=ordered.find(sku=>['square','feed_4x5_native']
    .some(variant=>!m6ReviewDrafts.has(sku+'/'+variant)));
  if(next)loadM6OCRReview(batchId,next);
});
$('m6ReviewAssess').addEventListener('click',async()=>{
  const source=m6ReviewSource,batchId=currentBatchId;
  if(!source||source.batchId!==batchId||m6OcrReviewBatch!==batchId||
     m6ReviewDrafts.size===0)return;
  const button=$('m6ReviewAssess');
  const draftSnapshot=JSON.stringify([...m6ReviewDrafts.values()]);
  button.disabled=true;
  invalidateM6ReviewAssessment();
  $('m6ReviewAssessmentStatus').textContent='正在核对已暂存标签、冻结审核表、OCR 与海报哈希…';
  try{
    // Keep all draft validation on the server; never treat browser counts as evidence.
    const exportedResponse=await fetch('/api/batches/'+encodeURIComponent(batchId)+
      '/m6-review-sidecar',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        reviews_csv_sha256:source.reviews_csv_sha256,
        ocr_capture_sha256:source.ocr_capture_sha256,
        decisions:JSON.parse(draftSnapshot)
      })
    });
    const exported=await exportedResponse.json();
    if(currentBatchId!==batchId||m6ReviewSource!==source||
       JSON.stringify([...m6ReviewDrafts.values()])!==draftSnapshot)return;
    if(!exportedResponse.ok)throw Error(errorText(exported));
    if(exported.status!=='OPERATOR_ASSERTED_NOT_ATTESTED')
      throw Error('审核声明状态无效');
    const response=await fetch('/api/batches/'+encodeURIComponent(batchId)+
      '/m6-review-sidecar/assess',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(exported)
    });
    const report=await response.json();
    if(currentBatchId!==batchId||m6ReviewSource!==source||
       JSON.stringify([...m6ReviewDrafts.values()])!==draftSnapshot)return;
    if(!response.ok)throw Error(errorText(report));
    if(!['PARTIAL','MANUAL_FAILURE_REPORTED','LABELS_COMPLETE_UNATTESTED'].includes(report.status)||
       report.quality_verified!==false||report.approved_for_publication!==false||
       report.operator_decision_count!==m6ReviewDrafts.size||
       report.reviews_csv_sha256!==source.reviews_csv_sha256||
       report.ocr_capture_sha256!==source.ocr_capture_sha256)
      throw Error('核对报告与当前草稿或冻结证据不一致');
    m6ReviewAssessment=report;
    $('m6ReviewDownloadAssessment').disabled=false;
    const next=report.missing_decisions?.[0];
    $('m6ReviewAssessmentStatus').textContent='本地证据核对：'+report.operator_decision_count+
      '/'+report.expected_output_count+' 条人工声明 · 缺失 '+report.missing_decision_count+
      ' · 人工 FAIL '+report.reported_fail_count+
      ' · 状态 '+report.status+
      (next?' · 下一待审 '+next.sku_id+' / '+next.variant_id:'')+
      '。未独立证明人工身份、真实 OCR、素材授权或商品质量；禁止据此投放。';
  }catch(err){
    if(currentBatchId===batchId&&m6ReviewSource===source)
      $('m6ReviewAssessmentStatus').textContent='核对失败，原草稿不变：'+err.message;
  }finally{
    if(currentBatchId===batchId&&m6ReviewSource===source)
      button.disabled=m6ReviewDrafts.size===0;
  }
});
$('m6ReviewDownloadAssessment').addEventListener('click',()=>{
  if(!m6ReviewAssessment||!m6ReviewSource||currentBatchId!==m6ReviewSource.batchId)return;
  const payload=JSON.stringify(m6ReviewAssessment,null,2)+'\n';
  const url=URL.createObjectURL(new Blob([payload],{type:'application/json'}));
  const anchor=document.createElement('a');
  anchor.href=url;anchor.download='m6-sidecar-assessment-'+currentBatchId+'.json';
  document.body.appendChild(anchor);anchor.click();anchor.remove();
  setTimeout(()=>URL.revokeObjectURL(url),30000);
});

$('m6ReviewExportSidecar').addEventListener('click',async()=>{
  const source=m6ReviewSource,batchId=currentBatchId;
  if(!source||source.batchId!==batchId||m6ReviewDrafts.size===0)return;
  const button=$('m6ReviewExportSidecar');
  button.disabled=true;
  $('m6ReviewSidecarStatus').textContent='正在复核冻结 OCR 与图片哈希后导出…';
  try{
    const response=await fetch('/api/batches/'+encodeURIComponent(batchId)+'/m6-review-sidecar',{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        reviews_csv_sha256:source.reviews_csv_sha256,
        ocr_capture_sha256:source.ocr_capture_sha256,
        decisions:[...m6ReviewDrafts.values()]
      })
    });
    const exported=await response.json();
    if(currentBatchId!==batchId||source!==m6ReviewSource)return;
    if(!response.ok)throw Error(errorText(exported));
    if(exported.status!=='OPERATOR_ASSERTED_NOT_ATTESTED')
      throw Error('服务器返回了意外的审核状态');
    const blob=new Blob([JSON.stringify(exported,null,2)+'\n'],{type:'application/json'});
    const url=URL.createObjectURL(blob);
    const anchor=document.createElement('a');
    anchor.href=url;
    anchor.download='m6-human-review-'+batchId+'.json';
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
    // Keep the blob alive until the browser has accepted the download.
    setTimeout(()=>URL.revokeObjectURL(url),30000);
    $('m6ReviewSidecarStatus').textContent='已生成 '+exported.decision_count+
      ' 条侧车记录，请保存下载文件。仅为操作员声明，不代表人工验证或质量 PASS。';
  }catch(err){
    if(currentBatchId===batchId)
      $('m6ReviewSidecarStatus').textContent='审核侧车导出失败：'+err.message;
  }finally{
    if(currentBatchId===batchId&&source===m6ReviewSource)
      button.disabled=m6ReviewDrafts.size===0;
  }
});

$('m6ReviewDownloadWorksheet').addEventListener('click',async()=>{
  const source=m6ReviewSource,batchId=currentBatchId;
  if(!m6ReviewEvidenceReady||!source||source.batchId!==batchId||
     m6OcrReviewBatch!==batchId||m6ReviewDrafts.size===0)return;
  const button=$('m6ReviewDownloadWorksheet');
  const snapshot=JSON.stringify([...m6ReviewDrafts.values()]);
  const stillCurrent=()=>m6ReviewEvidenceReady&&currentBatchId===batchId&&
    m6OcrReviewBatch===batchId&&source===m6ReviewSource&&
    JSON.stringify([...m6ReviewDrafts.values()])===snapshot;
  button.disabled=true;
  $('m6ReviewSidecarStatus').textContent='正在将已复核的审核声明转换为独立 CSV 草稿…';
  try{
    const base='/api/batches/'+encodeURIComponent(batchId)+'/m6-review-sidecar';
    const response=await fetch(base,{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({
        reviews_csv_sha256:source.reviews_csv_sha256,
        ocr_capture_sha256:source.ocr_capture_sha256,
        decisions:JSON.parse(snapshot)
      })
    });
    const exported=await response.json();
    if(!stillCurrent())return;
    if(!response.ok)throw Error(errorText(exported));
    if(exported.status!=='OPERATOR_ASSERTED_NOT_ATTESTED'||
       exported.reviews_csv_sha256!==source.reviews_csv_sha256||
       exported.ocr_capture_sha256!==source.ocr_capture_sha256)
      throw Error('侧车身份与冻结证据不一致');
    const worksheet=await fetch(base+'/worksheet',{
      method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(exported)
    });
    if(!stillCurrent())return;
    if(!worksheet.ok){
      const error=await worksheet.json();throw Error(errorText(error));
    }
    if(worksheet.headers.get('x-worksheet-status')!=='OPERATOR_ASSERTED_NOT_ATTESTED'||
       worksheet.headers.get('x-source-reviews-sha256')!==source.reviews_csv_sha256)
      throw Error('工作表不是可追溯的操作员草稿');
    const blob=await worksheet.blob();
    if(!stillCurrent())return;
    if(blob.size===0||blob.size>2000000)throw Error('工作表大小无效');
    const url=URL.createObjectURL(blob);
    const anchor=document.createElement('a');
    anchor.href=url;anchor.download='m6-operator-worksheet-draft.csv';
    document.body.appendChild(anchor);anchor.click();anchor.remove();
    setTimeout(()=>URL.revokeObjectURL(url),30000);
    $('m6ReviewSidecarStatus').textContent='已导出独立人工评审 CSV 草稿。审核时间仍为空；'+
      '文件尚未取得独立审核/授权证明，修改 CSV 后需重新建立匹配的 OCR 证据。';
  }catch(err){
    if(currentBatchId===batchId&&source===m6ReviewSource)
      $('m6ReviewSidecarStatus').textContent='评审 CSV 草稿导出失败：'+err.message;
  }finally{
    if(currentBatchId===batchId&&source===m6ReviewSource)
      button.disabled=!m6ReviewEvidenceReady||m6ReviewDrafts.size===0;
  }
});

$('inspectM6OCR').addEventListener('click',()=>{
  if(currentBatchId&&lastBatchStatus==='COMPLETED')
    loadM6OCRReview(currentBatchId);
});
$('m6OcrSkuPicker').addEventListener('change',()=>{
  if(currentBatchId&&m6OcrReviewBatch===currentBatchId)
    loadM6OCRReview(currentBatchId,$('m6OcrSkuPicker').value);
});

let m6OCRCaptureTimer=null;
async function refreshM6OCRCaptureStatus(batchId){
  if(currentBatchId!==batchId)return;
  const button=$('captureM6OCR'),label=$('m6OCRCaptureStatus');
  button.disabled=true;
  try{
    const response=await fetch('/api/batches/'+encodeURIComponent(batchId)+'/m6-ocr-capture');
    const result=await response.json();
    if(currentBatchId!==batchId)return;
    if(!response.ok)throw Error(errorText(result));
    button.disabled=!result.ready_to_capture;
    const states={
      NOT_CONFIGURED:'无法运行：本机缺 PaddleOCR 3.x/PaddlePaddle，或未配置有效的检测、识别模型绝对路径。请设置 POSTER_M6_OCR_DET_MODEL_DIR、POSTER_M6_OCR_REC_MODEL_DIR。',
      READY:'本机 OCR 已就绪。点击按钮后将离线运行整批方形和 4:5 海报，不会自动审批。',
      BUSY:'已有另一批本机 OCR 任务执行中；不能同时发起新任务。',
      RUNNING:'本批 OCR 正在本机执行；源图片和冻结 CSV 不会修改。',
      EVIDENCE_PRESENT_UNATTESTED:'已检测到完整且 SHA 一致的 OCR 采集声明；不能据此证明真实执行或质量通过。可继续生成标注并人工核对。',
      FAILED:'本次 OCR 采集失败；没有发布成功证据。请检查本地服务日志、模型和图片。',
      FAILED_NO_EVIDENCE:'OCR 未发布完整证据；检查本地日志后可重试。',
      BLOCKED:'本批 OCR 证据已存在但格式或摘要不匹配；禁止覆盖，请核查原始数据。'
    };
    label.textContent=states[result.state]||'OCR 状态：'+result.state;
    if(result.state==='RUNNING'){
      const done=result.completed_variants,expected=result.expected_variants;
      label.textContent=Number.isInteger(done)&&Number.isInteger(expected)&&
        expected>0&&done>=0&&done<=expected
        ? '本机 OCR 已完成 '+done+'/'+expected+' 个版式的识别；完成计数仅存在内存中，不是质量验收或持久化成功证明。'
        : '本机 OCR 正在初始化模型；实际识别尚未公布完成计数。';
    }
    if(['FAILED','FAILED_NO_EVIDENCE'].includes(result.state)&&result.ready_to_capture){
      label.textContent+=' 已允许显式重试；现有未完成结果不会作为成功证据。';
    }
    if(result.state==='RUNNING'){
      if(m6OCRCaptureTimer)clearTimeout(m6OCRCaptureTimer);
      m6OCRCaptureTimer=setTimeout(()=>refreshM6OCRCaptureStatus(batchId),2500);
    }else if(m6OCRCaptureTimer){
      clearTimeout(m6OCRCaptureTimer);m6OCRCaptureTimer=null;
    }
    if(result.state==='EVIDENCE_PRESENT_UNATTESTED')refreshM6OverlayStatus(batchId,true);
  }catch(err){
    if(currentBatchId===batchId){
      button.disabled=true;
      label.textContent='本地 OCR 预检失败：'+err.message;
    }
  }
}
$('captureM6OCR').addEventListener('click',async()=>{
  const batchId=currentBatchId;
  if(!batchId||lastBatchStatus!=='COMPLETED'||$('captureM6OCR').disabled)return;
  $('captureM6OCR').disabled=true;
  $('m6OCRCaptureStatus').textContent='正在提交整批 PaddleOCR 采集任务；不会自动批准海报…';
  try{
    const response=await fetch('/api/batches/'+encodeURIComponent(batchId)+'/m6-ocr-capture',{method:'POST'});
    const result=await response.json();
    if(currentBatchId!==batchId)return;
    if(!response.ok)throw Error(errorText(result));
    $('m6OCRCaptureStatus').textContent='本机 OCR 已启动。采集期间不能再次运行或覆盖原证据。';
    refreshM6OCRCaptureStatus(batchId);
  }catch(err){
    if(currentBatchId===batchId){
      $('m6OCRCaptureStatus').textContent='无法启动 OCR：'+err.message;
      refreshM6OCRCaptureStatus(batchId);
    }
  }
});

async function refreshM6OverlayStatus(batchId,skipCapture=false){
  if(!skipCapture)refreshM6OCRCaptureStatus(batchId);
  if(currentBatchId!==batchId)return;
  const label=$('m6OverlayStatus');
  label.textContent='正在检查本机 OCR 标注预览文件状态…';
  $('prepareM6Overlay').disabled=true;
  $('downloadM6Overlay').disabled=true;
  $('inspectM6OCR').disabled=true;
  try{
    const response=await fetch('/api/batches/'+encodeURIComponent(batchId)+'/m6-overlay');
    const result=await response.json();
    if(currentBatchId!==batchId)return;
    if(!response.ok)throw Error(errorText(result));
    $('prepareM6Overlay').disabled=!result.ocr_capture_present||result.overlay_prepared;
    $('downloadM6Overlay').disabled=!result.overlay_prepared;
    $('inspectM6OCR').disabled=!result.ocr_capture_present;
    label.textContent=result.overlay_prepared
      ? 'OCR 标注包已存在；可下载方形和 4:5 的检测框预览。仅供人工复核，不证明 OCR 质量通过。'
      : result.ocr_capture_present
        ? '已检测到本机 OCR 采集记录，可显式生成只读标注预览；不会执行 OCR、修改原图或自动批准。'
        : '尚未在本地审核包找到 OCR 采集记录。请先独立运行真实 PaddleOCR 采集。';
  }catch(err){
    if(currentBatchId===batchId){
      $('prepareM6Overlay').disabled=true;
      $('downloadM6Overlay').disabled=true;
      $('inspectM6OCR').disabled=true;
      resetM6OCRReview();
      label.textContent='OCR 标注包不可用：'+err.message;
    }
  }
}
$('prepareM6Overlay').addEventListener('click',async()=>{
  const batchId=currentBatchId;
  if(!batchId||lastBatchStatus!=='COMPLETED'||$('prepareM6Overlay').disabled)return;
  const button=$('prepareM6Overlay'),label=$('m6OverlayStatus');
  button.disabled=true;
  label.textContent='正在基于冻结 OCR 证据生成标注 ZIP；不会运行 OCR 或覆盖已有评审…';
  try{
    const response=await fetch('/api/batches/'+encodeURIComponent(batchId)+'/m6-overlay',{
      method:'POST'
    });
    const result=await response.json();
    if(currentBatchId!==batchId)return;
    if(!response.ok)throw Error(errorText(result));
    $('downloadM6Overlay').disabled=false;
    label.textContent='OCR 标注预览已生成：'+result.overlay_count+
      ' 张 · 已定位文字 '+result.positioned_line_count+
      ' 条 · 待人工定位 '+result.unlocated_line_count+
      ' 条。仅供目视核对，未进行质量验收。';
  }catch(err){
    if(currentBatchId===batchId){
      label.textContent='无法生成 OCR 标注包：'+err.message;
      // Do not re-run preparation blindly; inspect existing file state.
      await refreshM6OverlayStatus(batchId);
    }
  }
});
$('downloadM6Overlay').addEventListener('click',()=>{
  const batchId=currentBatchId;
  if(!batchId||lastBatchStatus!=='COMPLETED'||$('downloadM6Overlay').disabled)return;
  const link=document.createElement('a');
  link.href='/api/batches/'+encodeURIComponent(batchId)+'/m6-overlay/download';
  link.download='m6-ocr-overlay-'+batchId+'.zip';
  document.body.appendChild(link);
  link.click();
  link.remove();
});

$('reviewCurrentBatch').addEventListener('click',async()=>{
  const batchId=currentBatchId;
  if(!batchId || lastBatchStatus!=='COMPLETED')return;
  const button=$('reviewCurrentBatch');
  button.disabled=true;
  $('batchReviewScopeStatus').textContent='正在核对当前批次的完整导入账本…';
  try{
    const response=await fetch('/api/batches/'+encodeURIComponent(batchId)+'/review-scope');
    const data=await response.json();
    if(!response.ok)throw Error(errorText(data));
    if(currentBatchId!==batchId)return;
    if(!data.completed || data.batch_id!==batchId ||
       !BATCH_SHA.test(data.manifest_sha256) ||
       !Array.isArray(data.rows) || data.rows.length!==data.total_skus ||
       data.rows.length<1 || data.rows.length>100)
      throw Error('服务器返回的批次范围无效');
    const ids=new Map();
    for(const item of data.rows){
      if(!BATCH_SKU.test(item.sku_id) || !BATCH_POSTER_ID.test(item.poster_id) ||
         ids.has(item.poster_id))throw Error('批次 SKU 或海报 ID 格式不合法');
      ids.set(item.poster_id,item.sku_id);
    }
    batchReviewScope={ids:ids,manifest_sha256:data.manifest_sha256};
    $('batchReviewScopeStatus').textContent='当前批次已完成，限定审核 '+ids.size+
      ' 个 SKU（清单 SHA-256：'+data.manifest_sha256.slice(0,12)+
      '…）。只做浏览器筛选，不会运行 OCR 或自动批准。';
    if(lastReviewQueueData)renderReviewQueue(lastReviewQueueData);
    $('loadReviewQueue').click();
    showWorkspace('review',{focus:true});
    $('trialQueue').scrollIntoView({behavior:'smooth',block:'start'});
  }catch(err){
    if(currentBatchId===batchId)
      $('batchReviewScopeStatus').textContent='无法进入当前批次审核：'+err.message;
  }finally{
    if(currentBatchId===batchId)button.disabled=lastBatchStatus!=='COMPLETED';
  }
});

const batchHistoryPaging={limit:10,offset:0};
let batchHistoryRequest=0;
let batchHistorySelectionRequest=0;
async function selectHistoricalBatch(batchId){
  const request=++batchHistorySelectionRequest;
  $('batchHistoryInfo').textContent='正在打开历史批次…';
  try{
    const response=await fetch('/api/batches/'+encodeURIComponent(batchId));
    const state=await response.json();
    if(request!==batchHistorySelectionRequest)return;
    if(!response.ok)throw Error(errorText(state));
    if(batchPollTimer){clearTimeout(batchPollTimer);batchPollTimer=null;}
    batchPollGeneration+=1; // Invalidate polling of the old selected batch.
    if(currentBatchId!==batchId){
      batchReviewScope=null;
      $('batchReviewScopeStatus').textContent=
        '已切换历史批次。原审核筛选已清除；请为当前批次重新点击「审核当前批次」。';
      if(lastReviewQueueData)renderReviewQueue(lastReviewQueueData);
    }
    currentBatchId=batchId;
    renderBatchState(state);
    $('batchHistoryInfo').textContent='已打开批次 '+batchId.slice(0,10)+'…';
    await loadBatchHistory();
  }catch(err){
    if(request===batchHistorySelectionRequest){
      $('batchHistoryInfo').textContent='打开批次失败：'+err.message;
    }
  }
}
async function loadBatchHistory(){
  const request=++batchHistoryRequest;
  const offset=batchHistoryPaging.offset,limit=batchHistoryPaging.limit;
  const status=$('batchHistoryFilter').value;
  const params=new URLSearchParams({limit:String(limit),offset:String(offset)});
  if(status)params.set('status',status);
  $('batchHistoryPrev').disabled=true;
  $('batchHistoryNext').disabled=true;
  $('batchHistoryInfo').textContent='正在读取历史批次…';
  try{
    const response=await fetch('/api/batches?'+params.toString());
    const data=await response.json();
    if(request!==batchHistoryRequest)return;
    if(!response.ok)throw Error(errorText(data));
    const host=$('batchHistoryItems');
    host.replaceChildren();
    for(const item of data.items||[]){
      const row=document.createElement('div');
      row.className='execution-row';
      const summary=document.createElement('span');
      summary.className='summary';
      summary.textContent=batchStatusLabel(item.status)+' · '+item.processed+'/'+item.total+
        ' SKU · '+item.updated_at+' · '+item.batch_id.slice(0,10)+'…';
      row.appendChild(summary);
      const open=document.createElement('button');
      open.className='secondary';open.type='button';
      open.disabled=currentBatchId===item.batch_id;
      open.textContent=open.disabled?'当前批次':'查看批次';
      open.addEventListener('click',()=>selectHistoricalBatch(item.batch_id));
      row.appendChild(open);
      host.appendChild(row);
    }
    if(!(data.items||[]).length){
      host.textContent=offset?'本页无批次，请返回上一页':'暂无符合条件的历史批次';
    }
    $('batchHistoryPrev').disabled=offset===0;
    $('batchHistoryNext').disabled=!data.has_more||offset+limit>10000;
    const range=(data.items||[]).length
      ? '第 '+(offset+1)+'–'+(offset+data.items.length)+' 个'
      : '当前页无记录';
    $('batchHistoryInfo').textContent=range+
      (data.has_more?'，还有更多':'')+
      (data.skipped_invalid?' · 跳过 '+data.skipped_invalid+' 个损坏批次':'');
  }catch(err){
    if(request!==batchHistoryRequest)return;
    $('batchHistoryInfo').textContent='读取历史批次失败：'+err.message;
    $('batchHistoryPrev').disabled=offset===0;
  }
}
$('refreshBatchHistory').addEventListener('click',loadBatchHistory);
$('batchHistoryFilter').addEventListener('change',()=>{
  batchHistoryPaging.offset=0;
  loadBatchHistory();
});
$('batchHistoryPrev').addEventListener('click',()=>{
  batchHistoryPaging.offset=Math.max(0,batchHistoryPaging.offset-batchHistoryPaging.limit);
  loadBatchHistory();
});
$('batchHistoryNext').addEventListener('click',()=>{
  batchHistoryPaging.offset=Math.min(10000,batchHistoryPaging.offset+batchHistoryPaging.limit);
  loadBatchHistory();
});
loadBatchHistory();

(async function restoreLastBatch(){
  const saved=localStorage.getItem(BATCH_STORAGE_KEY);
  if(!saved||!/^[a-f0-9]{32}$/.test(saved))return;
  currentBatchId=saved;
  $('batchStatus').textContent='正在恢复上次批次状态…';
  await refreshBatchState(saved);
})();
refreshExportLink();

(async function restoreLastPoster(){
  const last=localStorage.getItem('poster-last-id');
  if(!last || !/^[a-f0-9]{32}$/.test(last))return;
  try{
    const response=await fetch('/api/posters/'+last);
    if(!response.ok)throw Error('poster unavailable');
    if(current)return;
    display(await response.json());
  }catch(e){
    localStorage.removeItem('poster-last-id');
    $('metadata').textContent='上次海报无法恢复，可以重新生成';
  }
})();
$('applyBatch').hidden=!currentBatchId;
showWorkspace(location.hash.slice(1));
setBatchStage(0);
