/* Offline Markdown subset. Raw HTML and links stay text; no remote resources. */
(function () {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function inline(value) {
    return String(value).split(/(`+[^`\n]+`+)/g).map(part => {
      if (part.startsWith('`')) return '<code>'+escape(part.replace(/^`+|`+$/g,''))+'</code>';
      return escape(part).replace(/\*\*([^*\n]+)\*\*/g,'<strong>$1</strong>');
    }).join('');
  }
  function render(value) {
    const lines=String(value ?? '').replace(/\r\n/g,'\n').split('\n'), blocks=[];
    let code=null, fence='', paragraph=[];
    const flush=()=>{if(paragraph.length){blocks.push('<p>'+paragraph.map(inline).join('<br>')+'</p>');paragraph=[];}};
    for(const line of lines) {
      const marker=line.match(/^(`{3,}|~{3,})/);
      if(code!==null){if(marker && marker[1][0]===fence[0] && marker[1].length>=fence.length){blocks.push('<pre><code>'+escape(code.join('\n'))+'</code></pre>');code=null;}else code.push(line);continue;}
      if(marker){flush();fence=marker[1];code=[];continue;}
      const heading=line.match(/^(#{1,6})\s+(.+)$/);
      if(heading){flush();blocks.push('<h'+heading[1].length+'>'+inline(heading[2])+'</h'+heading[1].length+'>');continue;}
      if(!line.trim()){flush();continue;}
      const item=line.match(/^[-*]\s+(.+)$/);
      if(item){flush();blocks.push('<div class="md-item">• '+inline(item[1])+'</div>');continue;}
      const quote=line.match(/^>\s?(.*)$/);
      if(quote){flush();blocks.push('<blockquote>'+inline(quote[1])+'</blockquote>');continue;}
      paragraph.push(line);
    }
    flush();if(code!==null)blocks.push('<pre><code>'+escape(code.join('\n'))+'</code></pre>');
    return blocks.join('');
  }
  window.SkillRadarMarkdown={render,inline};
})();
