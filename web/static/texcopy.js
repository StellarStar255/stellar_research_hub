// 复制时把公式变成好读的文字：简单公式转 Unicode（d_k → dₖ，\sqrt{d_k} → √dₖ），
// 转不干净的复杂公式才保留 LaTeX（$...$）。替代 KaTeX 官方 copy-tex（那个一律给 LaTeX，不好看）。
(() => {
  const SUB = {0:'₀',1:'₁',2:'₂',3:'₃',4:'₄',5:'₅',6:'₆',7:'₇',8:'₈',9:'₉',a:'ₐ',e:'ₑ',h:'ₕ',i:'ᵢ',j:'ⱼ',k:'ₖ',
    l:'ₗ',m:'ₘ',n:'ₙ',o:'ₒ',p:'ₚ',r:'ᵣ',s:'ₛ',t:'ₜ',u:'ᵤ',v:'ᵥ',x:'ₓ','+':'₊','-':'₋','=':'₌','(':'₍',')':'₎',
    'β':'ᵦ','γ':'ᵧ','ρ':'ᵨ','φ':'ᵩ','χ':'ᵪ',',':',',' ':''};
  const SUP = {0:'⁰',1:'¹',2:'²',3:'³',4:'⁴',5:'⁵',6:'⁶',7:'⁷',8:'⁸',9:'⁹',a:'ᵃ',b:'ᵇ',c:'ᶜ',d:'ᵈ',e:'ᵉ',f:'ᶠ',
    g:'ᵍ',h:'ʰ',i:'ⁱ',j:'ʲ',k:'ᵏ',l:'ˡ',m:'ᵐ',n:'ⁿ',o:'ᵒ',p:'ᵖ',r:'ʳ',s:'ˢ',t:'ᵗ',u:'ᵘ',v:'ᵛ',w:'ʷ',x:'ˣ',y:'ʸ',z:'ᶻ',
    T:'ᵀ',A:'ᴬ',B:'ᴮ',D:'ᴰ',E:'ᴱ',G:'ᴳ',H:'ᴴ',I:'ᴵ',J:'ᴶ',K:'ᴷ',L:'ᴸ',M:'ᴹ',N:'ᴺ',O:'ᴼ',P:'ᴾ',R:'ᴿ',U:'ᵁ',V:'ⱽ',W:'ᵂ',
    '+':'⁺','-':'⁻','−':'⁻','=':'⁼','(':'⁽',')':'⁾','*':'*','′':'′',"'":'′','⊤':'ᵀ','θ':'ᶿ',' ':''};
  const SYM = {
    alpha:'α',beta:'β',gamma:'γ',delta:'δ',epsilon:'ε',varepsilon:'ε',zeta:'ζ',eta:'η',theta:'θ',vartheta:'ϑ',iota:'ι',
    kappa:'κ',lambda:'λ',mu:'μ',nu:'ν',xi:'ξ',pi:'π',rho:'ρ',sigma:'σ',tau:'τ',upsilon:'υ',phi:'φ',varphi:'φ',chi:'χ',
    psi:'ψ',omega:'ω',Gamma:'Γ',Delta:'Δ',Theta:'Θ',Lambda:'Λ',Xi:'Ξ',Pi:'Π',Sigma:'Σ',Phi:'Φ',Psi:'Ψ',Omega:'Ω',
    cdot:'·',times:'×',div:'÷',pm:'±',mp:'∓',le:'≤',leq:'≤',ge:'≥',geq:'≥',ne:'≠',neq:'≠',approx:'≈',sim:'∼',simeq:'≃',
    equiv:'≡',propto:'∝',infty:'∞',sum:'∑',prod:'∏',int:'∫',partial:'∂',nabla:'∇',to:'→',rightarrow:'→',leftarrow:'←',
    Rightarrow:'⇒',Leftrightarrow:'⇔',mapsto:'↦',in:'∈',notin:'∉',subset:'⊂',subseteq:'⊆',cup:'∪',cap:'∩',forall:'∀',
    exists:'∃',circ:'∘',ldots:'…',dots:'…',cdots:'⋯',top:'⊤',perp:'⊥',odot:'⊙',otimes:'⊗',oplus:'⊕',langle:'⟨',rangle:'⟩',
    lVert:'‖',rVert:'‖',Vert:'‖',mid:'|',ell:'ℓ',hbar:'ħ',star:'⋆',prime:'′',
    log:'log',exp:'exp',ln:'ln',max:'max',min:'min',arg:'arg',argmax:'argmax',argmin:'argmin',sin:'sin',cos:'cos',
    tan:'tan',det:'det',lim:'lim',sup:'sup',inf:'inf',Pr:'Pr',E:'E',
    ',':' ',';':' ',':':' ','!':'',quad:'  ',qquad:'   ',' ':' ',left:'',right:'',big:'',Big:'',bigl:'',bigr:'',
    Bigl:'',Bigr:'',displaystyle:'',
  };
  const BB = {R:'ℝ',N:'ℕ',Z:'ℤ',Q:'ℚ',C:'ℂ',E:'𝔼',P:'ℙ'};

  // 读一个参数：{...} 或单个字符 / 命令
  function arg(s, i) {
    while (s[i] === ' ') i++;
    if (s[i] === '{') {
      let d = 0, j = i;
      for (; j < s.length; j++) { if (s[j] === '{') d++; else if (s[j] === '}' && --d === 0) break; }
      return [s.slice(i + 1, j), j + 1];
    }
    if (s[i] === '\\') { const m = /^\\([A-Za-z]+|.)/.exec(s.slice(i)); return [m[0], i + m[0].length]; }
    return [s[i] || '', i + 1];
  }
  const paren = t => [...t].length > 1 && !/^√?[\p{L}\p{N}\p{M}]+$/u.test(t) ? `(${t})` : t;
  // 分式 / 根号后面紧跟字母时空一格：d/dt u，而不是 d/dtu
  const gap = (s, i) => /[A-Za-z0-9\\]/.test(s[i] || '') ? ' ' : '';

  function conv(s) {
    let out = '', i = 0;
    while (i < s.length) {
      const c = s[i];
      if (c === '\\') {
        const m = /^\\([A-Za-z]+|.)/.exec(s.slice(i)); const name = m[1]; i += m[0].length;
        if (name === 'frac' || name === 'dfrac' || name === 'tfrac') {
          let a, b; [a, i] = arg(s, i); [b, i] = arg(s, i);
          const A = conv(a), B = conv(b); if (A == null || B == null) return null;
          out += `${paren(A)}/${paren(B)}` + gap(s, i);
        } else if (name === 'sqrt') {
          let a; [a, i] = arg(s, i); const A = conv(a); if (A == null) return null;
          out += '√' + paren(A) + gap(s, i);
        } else if (['text', 'mathrm', 'operatorname', 'textbf', 'mathit', 'mathbf', 'boldsymbol', 'bm', 'mathsf',
                    'mathcal', 'mathscr', 'textit', 'hat', 'bar', 'tilde', 'vec', 'widehat', 'overline'].includes(name)) {
          let a; [a, i] = arg(s, i);
          const A = ['text', 'textbf', 'textit'].includes(name) ? a : conv(a); if (A == null) return null;
          const acc = {hat: '̂', bar: '̄', tilde: '̃', vec: '⃗', widehat: '̂', overline: '̄'}[name];
          if (acc && [...A].length !== 1) return null;    // 多个字母上加帽子，Unicode 表示不了
          out += acc ? A + acc : A;
        } else if (name === 'mathbb') {
          let a; [a, i] = arg(s, i); if (!BB[a]) return null; out += BB[a];
        } else if (name in SYM) {
          out += SYM[name];
          if (/^[a-zA-Z]{2,}$/.test(SYM[name]) && /[A-Za-z0-9]/.test(s[i] || '')) out += ' ';
        } else if ('{}_^%$&#|'.includes(name)) out += name;
        else return null;                     // 认不得的命令：放弃，用 LaTeX
      } else if (c === '^' || c === '_') {
        let a; [a, i] = arg(s, i + 1);
        const A = conv(a); if (A == null) return null;
        const map = c === '^' ? SUP : SUB;
        const chars = [...A].map(ch => map[ch]);
        if (chars.some(x => x == null)) return null;   // 上下标里有不能转的字符（如 x_{ij}² 中的某些字母）
        out += chars.join('');
      } else if (c === '{' || c === '}') i++;
      else if (c === '&' || (c === '\\' && s[i + 1] === '\\')) return null;   // 矩阵 / 多行
      else { out += c === '-' ? '−' : c; i++; }
    }
    return out;
  }

  function texToText(tex, display) {
    let r = null;
    try { r = conv(tex.trim()); } catch { r = null; }
    if (r != null && !/[\\^_]/.test(r) && r.length <= 80) return r.replace(/\s+/g, ' ').replace(/ ([)\]},.;:])/g, '$1').trim();
    return display ? `$$${tex.trim()}$$` : `$${tex.trim()}$`;
  }
  window.texToText = texToText;

  document.addEventListener('copy', e => {
    const sel = getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) return;
    const range = sel.getRangeAt(0).cloneRange();
    const start = range.startContainer.parentElement?.closest('.katex');
    const end = range.endContainer.parentElement?.closest('.katex');
    if (start) range.setStartBefore(start);
    if (end) range.setEndAfter(end);
    const frag = range.cloneContents();
    if (!frag.querySelector('.katex')) return;
    frag.querySelectorAll('.katex').forEach(k => {
      const tex = k.querySelector('annotation[encoding="application/x-tex"]')?.textContent || k.textContent;
      k.replaceWith(document.createTextNode(texToText(tex, !!k.closest('.katex-display'))));
    });
    // 用 innerText 保留段落换行，需要临时挂到页面上
    const box = document.createElement('div');
    box.style.cssText = 'position:fixed;left:-99999px;top:0;white-space:pre-wrap';
    box.appendChild(frag);
    document.body.appendChild(box);
    const text = box.innerText;
    const html = box.innerHTML;
    box.remove();
    e.clipboardData.setData('text/plain', text);
    e.clipboardData.setData('text/html', html);
    e.preventDefault();
  });
})();
