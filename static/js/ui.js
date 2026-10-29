document.addEventListener('click',event=>{const toggle=event.target.closest('[data-password-toggle]');if(!toggle)return;const field=toggle.closest('.password-field');const input=field?.querySelector('input');if(!input)return;const show=input.type==='password';input.type=show?'text':'password';toggle.setAttribute('aria-label',show?'ซ่อนรหัสผ่าน':'แสดงรหัสผ่าน');toggle.setAttribute('aria-pressed',String(show));toggle.classList.toggle('is-visible',show);});
function toast(message){const node=document.querySelector('[data-toast]');if(!node)return;node.textContent=message;node.classList.add('show');clearTimeout(window.moodToastTimer);window.moodToastTimer=setTimeout(()=>node.classList.remove('show'),2600)}
function cartCount(count){document.querySelectorAll('[data-cart-count]').forEach(node=>node.textContent=Number(count).toLocaleString('th-TH'))}
document.addEventListener('submit',async event=>{const form=event.target.closest('[data-cart-form]');if(!form)return;event.preventDefault();const button=form.querySelector('button[type="submit"]');const label=button?.textContent;if(button){button.disabled=true;button.textContent='กำลังเพิ่ม...'}try{const response=await fetch(form.action,{method:'POST',body:new FormData(form)});const data=await response.json();if(!response.ok)throw new Error(data.error||'เพิ่มรายการไม่สำเร็จ');const source=form.dataset.cartSource||form.querySelector('[name=cart_source]')?.value||'menu';cartCount(data.count);if(source==='recommend'){const dock=document.querySelector('[data-recommend-cart]');if(dock){dock.hidden=false;const count=dock.querySelector('[data-recommend-cart-count]');const total=dock.querySelector('[data-recommend-cart-total]');if(count)count.textContent=Number(data.count).toLocaleString('th-TH')+' แก้ว';if(total)total.textContent=Number(data.total).toLocaleString('th-TH',{minimumFractionDigits:2,maximumFractionDigits:2})+' บาท · รวม VAT 7%';drawRecCart(data.cart)}}toast('เพิ่มลงตะกร้าแล้ว · '+Number(data.count).toLocaleString('th-TH')+' แก้ว')}catch(error){toast(error.message)}finally{if(button){button.disabled=false;button.textContent=label}}});
document.addEventListener('click',async event=>{const remove=event.target.closest('[data-remove-rec]');if(remove){const body=new FormData();body.append('drink_name',remove.dataset.name);body.append('style_name',remove.dataset.style);body.append('sweetness',remove.dataset.sweet);body.append('ice_separate',remove.dataset.ice);try{const response=await fetch('/remove-from-cart',{method:'POST',body});const data=await response.json();if(!response.ok)throw new Error(data.error||'ลบรายการไม่สำเร็จ');const dock=document.querySelector('[data-recommend-cart]');if(dock){dock.hidden=data.count===0;dock.querySelector('[data-recommend-cart-count]').textContent=Number(data.count).toLocaleString('th-TH')+' แก้ว';dock.querySelector('[data-recommend-cart-total]').textContent=Number(data.total).toLocaleString('th-TH',{minimumFractionDigits:2,maximumFractionDigits:2})+' บาท · รวม VAT 7%';drawRecCart(data.cart)}cartCount(data.count)}catch(error){toast(error.message)}return}const button=event.target.closest('[data-cart-action]');if(!button)return;button.disabled=true;try{const response=await fetch('/update-cart',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({index:Number(button.dataset.index),action:button.dataset.cartAction,cart_source:button.dataset.cartSource||'menu'})});const data=await response.json();if(!response.ok)throw new Error(data.error||'แก้ตะกร้าไม่สำเร็จ');cartCount(data.count);window.location.reload()}catch(error){toast(error.message);button.disabled=false}});
function drawRecCart(items){const list=document.querySelector('[data-rec-cart-list]');if(!list)return;list.replaceChildren();items.forEach(item=>{const row=document.createElement('div');const photo=document.createElement('span');photo.className='menu-photo menu-photo-thumb';const fallback=document.createElement('span');fallback.className='menu-photo-fallback';fallback.textContent='MENU';const image=document.createElement('img');image.src=item.image_url||('/static/images/menu/'+(item.image_key||'menu-default')+'.jpg?v=menu-set-20261014');image.alt=item.name;image.loading='lazy';image.onerror=()=>image.remove();image.onload=()=>photo.classList.add('has-image');photo.append(fallback,image);const label=document.createElement('span');label.textContent=item.name+' · '+Number(item.quantity).toLocaleString('th-TH')+' แก้ว';const button=document.createElement('button');button.type='button';button.textContent='×';button.setAttribute('aria-label','ลบ '+item.name);button.dataset.removeRec='';button.dataset.name=item.name;button.dataset.style=item.style_name;button.dataset.sweet=item.sweet;button.dataset.ice=item.ice_separate?'yes':'no';row.append(photo,label,button);list.append(row)})}

const trackingCard=document.querySelector('[data-track-order]');
if(trackingCard){
  const trackingCode=trackingCard.dataset.trackingCode;
  const statusNode=trackingCard.querySelector('[data-order-status]');
  const paymentNode=trackingCard.querySelector('[data-payment-status]');
  const trackingLive=trackingCard.querySelector('[data-tracking-live]');
  const trackingMessage=trackingCard.querySelector('[data-tracking-message]');
  let currentStatus=trackingCard.dataset.currentStatus;
  let resumeTrackingTimer;
  if(currentStatus==='เสร็จสิ้น'&&trackingLive){
    trackingLive.classList.remove('is-waiting');
    trackingLive.classList.add('is-complete');
    if(trackingMessage)trackingMessage.textContent='ออร์เดอร์เสร็จสิ้นแล้ว';
  }
  const paymentLabels={paid:'ชำระเงินแล้ว',pending:'รอร้านตรวจสอบสลิป',rejected:'สลิปไม่ผ่าน กรุณาติดต่อร้าน'};
  let trackingRequest;
  async function refreshTrackingStatus(){
    if(document.hidden||trackingRequest)return;
    trackingRequest=true;
    try{
      const response=await fetch('/api/track/'+encodeURIComponent(trackingCode),{headers:{Accept:'application/json'},cache:'no-store'});
      if(!response.ok)return;
      const latest=await response.json();
      if(statusNode)statusNode.textContent=latest.status;
      if(paymentNode)paymentNode.textContent=latest.payment_slip_submitted||latest.payment_status==='paid'?'ชำระเงินแล้ว':(paymentLabels[latest.payment_status]||latest.payment_status);
      if(latest.status!==currentStatus){
        currentStatus=latest.status;
        trackingCard.dataset.currentStatus=currentStatus;
        if(trackingLive){
          window.clearTimeout(resumeTrackingTimer);
          trackingLive.classList.remove('is-waiting','is-complete');
          trackingLive.classList.add('has-update');
          if(trackingMessage)trackingMessage.textContent=currentStatus==='เสร็จสิ้น'?'ออร์เดอร์เสร็จสิ้นแล้ว':'อัปเดตสถานะแล้ว: '+currentStatus;
          if(currentStatus==='เสร็จสิ้น')trackingLive.classList.add('is-complete');
          else resumeTrackingTimer=window.setTimeout(()=>{
            trackingLive.classList.remove('has-update');
            trackingLive.classList.add('is-waiting');
            if(trackingMessage)trackingMessage.textContent='กำลังรออัปเดตสถานะ';
          },1800);
        }
      }
    }catch(error){
      // Keep the last known status visible if the network is temporarily unavailable.
    }finally{trackingRequest=false}
  }
  refreshTrackingStatus();
  window.setInterval(refreshTrackingStatus,2500);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)refreshTrackingStatus()});
}





const adminOrderWatch=document.querySelector('[data-admin-order-watch]');
if(adminOrderWatch){
  let adminSignature=adminOrderWatch.dataset.adminOrderWatch;
  let adminRefreshPending=false;
  let adminCheckRunning=false;
  const adminRefreshStatus=adminOrderWatch.querySelector('[data-admin-refresh-status]');
  const adminHasEdits=()=>{
    const active=document.activeElement;
    if(active&&active.matches('input,textarea,select,[contenteditable="true"]'))return true;
    return [...document.querySelectorAll('.admin-section form[data-admin-dirty="true"]')].length>0;
  };
  const adminRefreshIfReady=()=>{
    if(!adminRefreshPending)return;
    if(adminHasEdits()){if(adminRefreshStatus)adminRefreshStatus.textContent='มีออร์เดอร์ใหม่ รอให้บันทึกฟอร์มที่กำลังแก้ก่อนอัปเดต';return;}
    adminRefreshPending=false;
    if(adminRefreshStatus)adminRefreshStatus.textContent='มีออร์เดอร์หรือข้อมูลใหม่ กำลังอัปเดต…';
    toast('มีออร์เดอร์หรือสถานะใหม่ กำลังอัปเดตหน้าแอดมิน');
    window.setTimeout(()=>window.location.reload(),650);
  };
  document.addEventListener('input',event=>{
    const form=event.target.closest('.admin-section form');
    if(form)form.dataset.adminDirty='true';
  });
  document.addEventListener('change',event=>{
    const form=event.target.closest('.admin-section form');
    if(form)form.dataset.adminDirty='true';
  });
  async function checkAdminOrders(){
    if(document.hidden||adminCheckRunning||adminRefreshPending)return;
    adminCheckRunning=true;
    try{
      const response=await fetch('/api/admin/orders-version',{headers:{Accept:'application/json'},cache:'no-store'});
      if(!response.ok)return;
      const latest=await response.json();
      if(latest.signature!==adminSignature){
        adminSignature=latest.signature;
        adminRefreshPending=true;
        adminRefreshIfReady();
      }
    }catch(error){
      if(adminRefreshStatus)adminRefreshStatus.textContent='กำลังเชื่อมต่อระบบอัปเดต…';
    }finally{adminCheckRunning=false;}
  }
  checkAdminOrders();
  window.setInterval(checkAdminOrders,4000);
  document.addEventListener('visibilitychange',()=>{if(!document.hidden)checkAdminOrders()});
  document.addEventListener('focusout',()=>window.setTimeout(adminRefreshIfReady,250));
}




document.addEventListener('DOMContentLoaded',()=>{const styleSelect=document.querySelector('#style');const iceOption=document.querySelector('[data-ice-option]');if(!styleSelect||!iceOption)return;const checkbox=iceOption.querySelector('input[name="ice_separate"]');const note=iceOption.querySelector('[data-ice-option-note]');const updateIceOption=()=>{const selectedStyle=styleSelect.value;const showIceOption=selectedStyle==='iced'||selectedStyle==='any';iceOption.hidden=!showIceOption;if(!showIceOption&&checkbox)checkbox.checked=false;if(note)note.textContent=selectedStyle==='any'?'(มีผลเฉพาะเมนูที่แนะนำเป็นแบบเย็น)':'(เฉพาะเครื่องดื่มเย็น)'};styleSelect.addEventListener('change',updateIceOption);updateIceOption()});

const logoutModal=document.querySelector('[data-logout-modal]');if(logoutModal){const openLogout=document.querySelector('[data-open-logout]');const closeLogout=logoutModal.querySelectorAll('[data-close-logout]');const dialog=logoutModal.querySelector('[role=dialog]');const closeModal=()=>{logoutModal.hidden=true;document.body.classList.remove('logout-modal-open');openLogout?.focus()};openLogout?.addEventListener('click',()=>{logoutModal.hidden=false;document.body.classList.add('logout-modal-open');dialog?.focus()});closeLogout.forEach(button=>button.addEventListener('click',closeModal));logoutModal.addEventListener('click',event=>{if(event.target===logoutModal)closeModal()});document.addEventListener('keydown',event=>{if(!logoutModal.hidden&&event.key==='Escape')closeModal()})}
