/* 安全检查的版本选择、操作清单和逐项结果；始终使用完整位置提交。 */
(function () {
  'use strict';
  window.SkillRadarProcessing = function (host) {
    const {tr,escapeHtml:esc,number,updateHTML}=host;
    const $=id=>document.getElementById(id), selected=new Map();
    let filtered=[],panel=null,submitting=false,submittedJob=null,tab='checks';
    const labels={rescan:'重新检查',review:'确认已查看',trust:'信任当前版本',revoke_trust:'撤销信任',quarantine:'放入隔离区',restore:'恢复并重新检查',restore_trust:'恢复并信任当前版本'};
    const effects={
      rescan:'客户端会逐项完成检查，提交成功后仍需等待检查结果。',
      review:'记录你已查看当前内容，保留风险提示和自动隔离规则。',
      trust:'停止当前版本的重复提醒和自动隔离，保留风险详情；内容变化后信任失效。',
      revoke_trust:'撤销当前版本的信任，重新检查并恢复提醒和自动隔离规则。',
      quarantine:'将这些技能移出原文件夹，保存到本机隔离区；随后可以恢复。',
      restore:'恢复到原文件夹并重新检查。风险仍存在时，可能再次自动隔离；同名文件夹不会被覆盖。',
      restore_trust:'恢复到原文件夹并信任当前版本，停止该版本的重复提醒和自动隔离，保留风险详情；同名文件夹不会被覆盖。',
    };
    const explanations={ok:'处理完成',version_changed:'内容已变化，请重新查看后再处理',not_registered:'该技能不在当前检查范围',missing:'文件已不在原位置',scan_incomplete:'检查未完成，请重新检查',restore_conflict:'原位置已有同名文件夹，未覆盖任何文件',move_failed:'文件移动失败，请核对位置与访问权限',metadata_failed:'处理记录保存失败，请核对实际文件位置',scan_failed:'检查失败，请稍后重新检查',unsafe_path:'文件位置无法安全确认，请重新查看',interrupted:'上次处理被中断，可以重试失败项',job_running:'已有处理任务正在进行，请等待完成',invalid_action:'客户端不支持此操作，请更新客户端',invalid_items:'选择信息不完整，请重新选择',operation_failed:'操作未完成，请核对最新记录后重试',note_failed:'文件已移动，隔离说明保存失败；请核对实际文件位置',record_update_failed:'文件已移动，处理记录更新失败；请核对实际文件位置'};
    explanations.copy_pending='隔离未完成，原文件仍在，请重试隔离';
    explanations.restore_copy_pending='已恢复，隔离副本待清理；原位置和隔离区均有文件，请手动核对';
    explanations.stopping='客户端正在退出，此项尚未处理';
    const version=row=>String(row?.version || '');
    const nameFor=path=>String(path || '').replace(/[\\/]+$/,'').split(/[\\/]/).pop();
    const actionButton=(action,id='')=>'<button type="button" class="btn '+(action==='quarantine'?'danger':'')+'" '+(id?'id="'+id+'" ':'')+'data-batch-action="'+action+'">'+esc(tr(labels[action]))+'</button>';
    const parent=$('skill-search').closest('.card');
    const tabs=document.createElement('div');tabs.className='security-tabs';
    tabs.innerHTML='<div role="tablist" aria-label="'+esc(tr('安全检查内容'))+'"><button class="btn" id="tab-checks" role="tab" aria-selected="true">'+esc(tr('检查结果'))+'</button><button class="btn" id="tab-quarantine" role="tab" aria-selected="false">'+esc(tr('隔离区'))+'</button></div><button class="btn" id="btn-batch-results" hidden>'+esc(tr('查看处理结果'))+'</button>';
    parent.before(tabs);
    const storeError=document.createElement('div');storeError.id='processing-error';storeError.className='processing-effect warning';storeError.setAttribute('role','alert');storeError.hidden=true;tabs.after(storeError);
    const selectLine=document.createElement('label');selectLine.className='selection-line';
    selectLine.innerHTML='<input type="checkbox" id="select-filtered"><span id="select-filtered-label">'+esc(tr('全选当前筛选结果'))+'</span>';
    $('sec-rows').closest('table').before(selectLine);
    const toolbar=document.createElement('div');toolbar.id='batch-toolbar';toolbar.className='batch-toolbar';toolbar.hidden=true;
    toolbar.innerHTML='<strong id="selection-count"></strong><div class="batch-buttons">'+['rescan','review','trust','quarantine','revoke_trust'].map(action=>actionButton(action)).join('')+'</div><button class="btn ghost" id="btn-clear-selection">'+esc(tr('清空选择'))+'</button>';
    selectLine.after(toolbar);
    const quarantine=document.createElement('div');quarantine.id='quarantine-panel';quarantine.className='card';quarantine.hidden=true;
    quarantine.innerHTML='<div class="card-head"><h2 id="quarantine-title">'+esc(tr('隔离区'))+'</h2><span id="quarantine-count" class="meta"></span></div><div id="quarantine-rows" style="padding:0 18px 18px"></div>';
    parent.after(quarantine);
    const detail=document.createElement('div');detail.id='detail-processing';detail.className='processing-actions';detail.hidden=true;$('drawer').querySelector('footer').append(detail);
    const panelActions=document.createElement('div');panelActions.id='processing-panel-actions';panelActions.className='processing-actions';panelActions.hidden=true;
    panelActions.innerHTML='<button type="button" class="btn" id="btn-cancel-processing">'+esc(tr('返回'))+'</button><button type="button" class="btn primary" id="btn-confirm-processing">'+esc(tr('确认处理'))+'</button><button type="button" class="btn primary" id="btn-retry-failed" hidden>'+esc(tr('仅重试失败项'))+'</button>';
    detail.after(panelActions);

    function itemsSnapshot(items) {
      return items.map(item=>{
        const row=host.getState()?.skills?.[item.path],record=(host.getState()?.quarantine || []).find(q=>q.id===item.id);
        const findings=row?.raw_findings || row?.findings || record?.raw_findings || record?.findings || [];
        return {item:{...item},name:row?.name || record?.name || nameFor(item.path),
          critical:findings.some(f=>f.severity==='CRITICAL'),
          riskCount:findings.length,
          selectedVersion:version(row),quarantine:record?{...record}:null};
      });
    }
    function currentJob() {
      const job=host.getState()?.batch_job;
      if(submittedJob && (!job || submittedJob.id!==job.id))return submittedJob;
      return job || submittedJob;
    }
    function working() {return submitting || currentJob()?.status==='running' || !!host.getState()?.processing_error;}
    function restoreSelectionProblem() {
      if(panel?.mode!=='confirm' || !['restore','restore_trust'].includes(panel.action))return '';
      for(const item of panel.items) {
        const q=(host.getState()?.quarantine || []).find(record=>record.id===item.id);
        if(q?.refreshing)return '正在更新隔离检查结果，完成后可恢复';
        if(!q || q.status==='restore_copy_pending' || q.status==='copy_pending' || q.version!==item.version)return '隔离检查结果已更新，请关闭清单并重新查看后恢复';
      }
      return '';
    }
    function clear() {selected.clear();}
    function checkbox(path,row) {
      return '<input type="checkbox" class="skill-select" data-path="'+esc(path)+'" aria-label="'+esc(tr('选择技能：')+(row.name || nameFor(path)))+'" '+(selected.has(path)?'checked ':'')+(!version(row)?'disabled ':'')+'>';
    }
    function updated(path,row) {return selected.has(path)&&selected.get(path).version!==version(row)?'<span class="batch-updated">'+esc(tr('内容已更新，处理时将跳过'))+'</span>':'';}
    function quarantineRow(q) {
      const pending=q.status==='copy_pending',restoredCopy=q.status==='restore_copy_pending';
      return '<article class="processing-item"><h3>'+esc(q.name || nameFor(q.original_path))+'</h3><span class="badge warn">'+esc(tr(pending?'隔离未完成':restoredCopy?'已恢复，隔离副本待清理':'已隔离'))+'</span>'+(pending || restoredCopy?'<p class="note" style="margin-top:8px">'+esc(tr(explanations[pending?'copy_pending':'restore_copy_pending']))+'</p>':'')+(q.refreshing?'<p class="note" style="margin-top:8px">'+esc(tr('正在更新隔离检查结果，完成后可恢复'))+'</p>':'')+'<p class="quarantine-meta">'+esc(tr('隔离时间：'))+esc(q.isolated_at || tr('未记录'))+'</p><p class="quarantine-meta">'+esc(tr('隔离原因：'))+esc(q.reason==='automatic'?tr('发现严重风险后自动隔离'):q.reason==='manual'?tr('人工隔离'):q.reason || tr('人工隔离'))+'</p><p class="skill-path">'+esc(tr('原位置：'))+esc(q.original_path)+'</p><p class="skill-path">'+esc(tr('隔离位置：'))+esc(q.destination || q.path)+'</p><div class="quarantine-actions">'+(restoredCopy?'':pending?'<button class="btn" data-quarantine-retry="'+esc(q.id)+'">'+esc(tr('重试隔离'))+'</button>':['restore','restore_trust'].map(action=>'<button class="btn" data-restore-action="'+action+'" data-record="'+esc(q.id)+'">'+esc(tr(labels[action]))+'</button>').join(''))+'</div></article>';
    }
    function render(rows) {
      if(submittedJob && host.getState()?.batch_job?.id===submittedJob.id)submittedJob=null;
      filtered=rows;
      const selectable=rows.filter(([,row])=>version(row));
      const checked=selectable.filter(([path])=>selected.has(path)).length;
      $('select-filtered').checked=!!selectable.length && checked===selectable.length;
      $('select-filtered').indeterminate=checked>0 && checked<selectable.length;
      $('select-filtered').disabled=!host.connected() || !selectable.length;
      $('select-filtered-label').textContent=tr('全选当前筛选结果');
      $('selection-count').textContent=tr('已选择 ')+number(selected.size)+tr(' 个技能');
      $('btn-clear-selection').textContent=tr('清空选择');$('batch-toolbar').hidden=!selected.size;
      const job=currentJob();$('btn-batch-results').hidden=!job;
      storeError.hidden=!host.getState()?.processing_error;
      storeError.textContent=tr('处理记录暂时不可用，已暂停新的处理操作；请稍后重试或重新启动客户端。');
      $('btn-batch-results').textContent=tr(job?.status==='running'?'查看处理进度':'查看处理结果');
      $('tab-checks').textContent=tr('检查结果');$('tab-quarantine').textContent=tr('隔离区');$('quarantine-title').textContent=tr('隔离区');
      tabs.querySelector('[role="tablist"]').setAttribute('aria-label',tr('安全检查内容'));
      parent.hidden=tab!=='checks';quarantine.hidden=tab!=='quarantine';
      const records=Array.isArray(host.getState()?.quarantine)?host.getState().quarantine:[];
      $('quarantine-count').textContent=number(records.length)+tr(' 个技能');
      updateHTML('quarantine-rows',records.length?records.map(quarantineRow).join(''):'<div class="state-message"><strong>'+esc(tr('隔离区为空'))+'</strong><p>'+esc(tr('放入隔离区的技能会显示在这里，可以恢复到原位置。'))+'</p></div>');
      availability();
      if(panel)renderPanel();
    }
    function availability() {
      $('select-filtered').disabled=!host.connected() || !filtered.some(([,row])=>version(row));
      for(const input of $('sec-rows').querySelectorAll('.skill-select'))input.disabled=!host.connected() || !version(host.getState()?.skills?.[input.dataset.path]);
      for(const button of document.querySelectorAll('[data-batch-action],[data-restore-action],[data-quarantine-retry]'))button.disabled=!host.connected() || working();
      for(const button of document.querySelectorAll('[data-restore-action]')) {
        const q=(host.getState()?.quarantine || []).find(record=>record.id===button.dataset.record);
        button.disabled=button.disabled || !!q?.refreshing;
      }
      for(const button of $('batch-toolbar').querySelectorAll('[data-batch-action]')) {
        button.textContent=tr(labels[button.dataset.batchAction]);
        button.hidden=button.dataset.batchAction==='revoke_trust' && ![...selected.keys()].some(path=>host.getState()?.skills?.[path]?.review_status==='trusted');
      }
      if($('btn-confirm-processing'))$('btn-confirm-processing').disabled=!host.connected() || working() || !!restoreSelectionProblem();
      if($('btn-retry-failed'))$('btn-retry-failed').disabled=!host.connected() || working();
    }
    function detailFor(path,row) {
      const modern=!!row?.version;
      detail.hidden=!modern;
      $('drawer').querySelector('.drawer-actions').hidden=modern;
      panelActions.hidden=true;
      $('btn-show-diff').hidden=false;
      if(modern) {
        updateHTML('detail-processing',['rescan',...(row.review_status==='not_required'?[]:['review']),'trust','quarantine',...(row.review_status==='trusted'?['revoke_trust']:[])].map(action=>actionButton(action)).join('')+
          (row.status==='drifted'?'<button type="button" class="btn" id="btn-detail-diff">'+esc(tr('查看内容变更'))+'</button>':''));
        detail.dataset.path=path;
        const diff=$('btn-detail-diff');if(diff)diff.onclick=()=>$('btn-show-diff').click();
      }
      availability();
    }
    function confirm(action,items,opener=document.activeElement) {
      if(!items.length || !host.connected() || working())return;
      panel={mode:'confirm',action,items:items.map(i=>({...i})),snapshot:itemsSnapshot(items)};
      host.openPanel(opener);renderPanel();
    }
    function results(opener=document.activeElement) {if(!currentJob())return;panel={mode:'results'};host.openPanel(opener);renderPanel();}
    function resultDetails(result) {
      const stamp=result.scanned_at?'<p class="note">'+esc(tr('检查时间','Checked at')+' '+result.scanned_at)+'</p>':'';
      const issues=Array.isArray(result.scan_issues)?result.scan_issues:[];
      const details=Array.isArray(result.scan_gap_details)?result.scan_gap_details:(Array.isArray(result.raw_findings)?result.raw_findings.filter(f=>f.rule_id==='SR-OBFUS-004'):[]);
      return stamp+issues.map(issue=>'<p class="note">'+esc(tr('检查缺口：','Coverage gap: ')+(host.scanIssueText?host.scanIssueText(issue):issue))+'</p>').join('')+
        details.map(detail=>'<p class="note">'+esc((detail.file || '')+(detail.line?' · '+tr('第 ','Line ')+detail.line+tr(' 行',''):'')+' · '+(detail.message || ''))+'</p>').join('');
    }
    function renderPanel() {
      if(!panel)return;
      $('drawer').querySelector('.drawer-actions').hidden=true;detail.hidden=true;panelActions.hidden=false;$('accept-help').hidden=true;
      $('drawer-context').textContent=tr(panel.mode==='confirm'?'处理清单':'处理结果');
      $('btn-cancel-processing').textContent=tr(panel.mode==='confirm'?'返回':'关闭');
      $('btn-cancel-processing').hidden=false;$('btn-confirm-processing').hidden=panel.mode!=='confirm';$('btn-retry-failed').hidden=true;
      $('btn-confirm-processing').textContent=tr(labels[panel.action] || '确认处理');
      if(panel.mode==='confirm') {
        $('drawer-title').textContent=tr(labels[panel.action]);
        const risky=['trust','quarantine','restore','restore_trust','revoke_trust'].includes(panel.action);
        const scopeText=panel.action==='rescan'?tr(' 个技能。重新检查将读取这些位置的当前内容。',' skills. Rechecking reads the current content at these locations.'):tr(' 个技能。每项会再次核对当前内容，变化的项目将跳过。');
        const html='<p class="processing-effect '+(risky?'warning':'')+'">'+esc(tr(effects[panel.action]))+'</p><p class="note" style="margin-top:12px">'+esc(tr('将处理以下 '))+number(panel.items.length)+esc(scopeText)+'</p><div class="processing-list">'+panel.snapshot.map(s=>'<article class="processing-item"><h3>'+esc(s.name)+'</h3><p class="skill-path">'+esc(s.item.path)+'</p>'+ (s.critical?'<span class="badge crit">'+esc(tr('严重风险'))+'</span>':s.riskCount?'<span class="badge warn">'+esc(tr('发现风险提示'))+'</span>':'')+(s.quarantine?'<p class="skill-path">'+esc(tr('隔离位置：'))+esc(s.quarantine.destination || s.quarantine.path)+'</p>':panel.action!=='rescan' && version(host.getState()?.skills?.[s.item.path])!==s.item.version?'<p class="batch-updated">'+esc(tr('内容已更新，处理时将跳过'))+'</p>':'')+'</article>').join('')+'</div>';
        const problem=restoreSelectionProblem();
        updateHTML('drawer-body',(problem?'<p class="processing-effect warning" style="margin-bottom:12px">'+esc(tr(problem))+'</p>':'')+html);
      } else {
        const job=currentJob();if(!job)return;
        const list=Array.isArray(job.results)?job.results:[];
        const done=job.status==='done', failed=list.filter(r=>r.status==='failed');
        $('drawer-title').textContent=tr(labels[job.action] || '处理结果');
        $('btn-retry-failed').hidden=!done || !failed.length;
        $('btn-retry-failed').textContent=tr('仅重试失败项')+' ('+number(failed.length)+')';
        const label={success:'成功',failed:'失败',skipped:'跳过'};
        const saveWarning=job.persistence_error?'<p class="processing-effect warning" style="margin-top:12px">'+esc(tr('进度记录未保存，实际结果见下方'))+'</p>':'';
        const hasWarnings=list.some(r=>r.status==='success' && ['note_failed','record_update_failed','restore_copy_pending'].includes(r.code));
        const rollbackWarning=list.some(r=>r.rollback_error)?'<p class="processing-effect warning" style="margin-top:12px">'+esc(tr('恢复失败后的撤销信任未成功，请立即核对该技能的信任状态。'))+'</p>':'';
        const counters=['success','failed','skipped'].map(status=>tr(label[status])+': '+number(list.filter(r=>r.status===status).length)).join(' · ');
        const html='<p class="processing-effect">'+esc(tr(done?'处理已完成，请核对逐项结果。':'正在逐项处理，关闭此面板后仍可查看进度。'))+'</p>'+saveWarning+rollbackWarning+(hasWarnings?'<p class="note" style="margin-top:12px">'+esc(tr('部分文件已移动，但说明或处理记录需要核对。'))+'</p>':'')+'<div class="processing-progress"><p><strong>'+esc(tr(done?'处理完成':'处理中'))+'</strong><span>'+number(job.completed)+' / '+number(job.total)+'</span></p><progress max="'+Number(job.total || 1)+'" value="'+Number(job.completed || 0)+'"></progress><p class="note">'+esc(counters)+'</p></div><div class="processing-list">'+list.map(result=>'<article class="processing-item"><h3>'+esc(nameFor(result.path))+'</h3><p class="skill-path">'+esc(result.path)+'</p><span class="badge '+(result.status==='failed'?'crit':result.status==='skipped' || ['note_failed','record_update_failed','restore_copy_pending','scan_incomplete'].includes(result.code)?'warn':'healthy')+'">'+esc(result.code==='scan_incomplete'?tr('本轮检查结束，仍有检查缺口','Check finished with coverage gaps'):tr(label[result.status] || '待处理'))+'</span><p class="note" style="margin-top:8px">'+esc(result.code==='scan_incomplete'?tr('本轮检查已执行，覆盖缺口仍需处理。','This check ran; coverage gaps still need attention.'):tr(explanations[result.code] || '请核对该技能的最新记录'))+'</p>'+resultDetails(result)+'</article>').join('')+'</div>';
        updateHTML('drawer-body',html);
      }
      availability();
    }
    async function submit() {
      if(!panel || panel.mode!=='confirm' || submitting || !host.connected() || working() || restoreSelectionProblem())return;
      const {action,items}=panel;submitting=true;availability();$('btn-confirm-processing').textContent=tr('处理中…');
      try {
        const response=await host.act('batch_action',{action,items});
        if(response?.ok && response.job_id) {
          submittedJob={id:response.job_id,action,status:'running',total:items.length,completed:0,items,results:[]};
          clear();panel={mode:'results'};renderPanel();host.redraw();await host.tick();
        } else host.toast(tr('处理失败：')+tr(explanations[response?.code] || '无法提交处理，请稍后重试'),true);
      } finally {submitting=false;if(panel)renderPanel();availability();}
    }
    function dismiss() {
      panel=null;$('accept-help').hidden=false;$('drawer-context').textContent=tr('技能检查详情');
      panelActions.hidden=true;detail.hidden=true;$('drawer').querySelector('.drawer-actions').hidden=false;
    }
    $('sec-rows').addEventListener('change',event=>{
      const input=event.target.closest('.skill-select');if(!input)return;
      const path=input.dataset.path,row=host.getState()?.skills?.[path];
      if(input.checked && version(row))selected.set(path,{path,version:version(row)});else selected.delete(path);
      host.redraw();
    });
    $('select-filtered').addEventListener('change',event=>{
      if(event.target.checked)for(const[path,row]of filtered){if(version(row)&&!selected.has(path))selected.set(path,{path,version:version(row)});}
      else for(const[path]of filtered)selected.delete(path);
      host.redraw();
    });
    $('btn-clear-selection').onclick=()=>{clear();host.redraw();};
    toolbar.addEventListener('click',event=>{const button=event.target.closest('[data-batch-action]');if(button&&!button.disabled)confirm(button.dataset.batchAction,[...selected.values()],button);});
    detail.addEventListener('click',event=>{const button=event.target.closest('[data-batch-action]'),path=detail.dataset.path,row=host.getState()?.skills?.[path];if(button&&!button.disabled && version(row))confirm(button.dataset.batchAction,[{path,version:version(row)}],button);});
    quarantine.addEventListener('click',event=>{const button=event.target.closest('[data-restore-action]');if(!button || button.disabled)return;const q=(host.getState()?.quarantine || []).find(q=>q.id===button.dataset.record);if(q)confirm(button.dataset.restoreAction,[{path:q.original_path,id:q.id,version:q.version}],button);});
    quarantine.addEventListener('click',event=>{const button=event.target.closest('[data-quarantine-retry]');if(!button || button.disabled)return;const q=(host.getState()?.quarantine || []).find(q=>q.id===button.dataset.quarantineRetry);if(q)confirm('quarantine',[{path:q.original_path,version:q.version}],button);});
    for(const value of ['checks','quarantine'])$('tab-'+value).onclick=()=>{clear();tab=value;for(const other of ['checks','quarantine'])$('tab-'+other).setAttribute('aria-selected',String(other===value));host.redraw();};
    $('btn-batch-results').onclick=()=>results($('btn-batch-results'));
    $('btn-cancel-processing').onclick=host.closePanel;$('btn-confirm-processing').onclick=submit;
    $('btn-retry-failed').onclick=()=>{
      const job=currentJob();if(!job || working())return;
      const failures=(job.results || []).filter(r=>r.status==='failed').map(result=>{
        const item=(job.items || []).find(item=>item.path===result.path&&(!result.id || item.id===result.id));
        return item?{...item}:{path:result.path,version:result.version,...(result.id?{id:result.id}:{})};
      });
      confirm(job.action,failures,$('btn-retry-failed'));
    };
    return {checkbox,updated,render,availability,clear,detail:detailFor,renderPanel,isPanel:()=>!!panel,dismiss};
  };
})();
