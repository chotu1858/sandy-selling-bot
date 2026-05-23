
import logging,os
from datetime import datetime
from telegram import Update,InlineKeyboardButton,InlineKeyboardMarkup,ReplyKeyboardMarkup
from telegram.ext import Application,CommandHandler,MessageHandler,CallbackQueryHandler,ContextTypes,filters
BOT_TOKEN="8993139884:AAFu9k_nMKGnedkzAVo-s2lZ34CI1jTjWvI"
ADMIN_IDS=[8738016341]
ADMIN_USERNAME="@FLEXING7"
UPI_ID="harsha83@fam"
registered_users=set()
user_balances={}
referred_by={}
coupons={}
ff_stock={}
stock_counter=0
materials={"101":{"name":"BOT","stock":3.0},"102":{"name":"INDO","stock":10.0}}
products={"1":{"name":"BOT","price":"100","materials":{"101":1.0}},"2":{"name":"INDO","price":"150","materials":{"102":1.0}}}
pending_orders={}
order_counter=1000
ban_list=set()
vip_users=set()
support_tickets={}
ticket_counter=0
logging.basicConfig(format="%(asctime)s-%(levelname)s-%(message)s",level=logging.INFO)
logger=logging.getLogger(__name__)
def is_admin(uid):return uid in ADMIN_IDS
def is_banned(uid):return uid in ban_list
async def start(update,context):
 user=update.effective_user
 if is_banned(user.id):await update.message.reply_text("🚫 Aap banned hain!");return
 registered_users.add(user.id)
 if context.args and context.args[0].startswith("ref_"):
  try:
   rid=int(context.args[0].split("_")[1])
   if rid!=user.id and user.id not in referred_by:referred_by[user.id]=rid
  except:pass
 vip="👑 VIP Member\n" if user.id in vip_users else ""
 txt=(f"✨ *Hello {user.first_name}! Welcome to Auto-Shop* ✨\n{vip}\n"
  f"📦 *Commands:*\n/products - Products dekho\n/buy [id] - Kharidlo\n"
  f"/wallet - Apna wallet dekho\n/ticket [msg] - Support ticket\n"
  f"/affiliate - Paisa kamao\n/reseller - Reselling karo\n"
  f"/payment - UPI info\n/coupon [code] - Coupon lagao\n\n"
  f"👨‍💻 Support: {ADMIN_USERNAME}")
 try:
  p=await context.bot.get_user_profile_photos(user_id=user.id,limit=1)
  if p and p.photos:await update.message.reply_photo(photo=p.photos[0][-1].file_id,caption=txt,parse_mode="Markdown")
  else:await update.message.reply_text(txt,parse_mode="Markdown")
 except:await update.message.reply_text(txt,parse_mode="Markdown")
async def new_member_welcome(update,context):
 for m in update.message.new_chat_members:
  if m.is_bot:continue
  txt=f"✨ *Welcome {m.first_name}!* ✨\n\n🆔 `{m.id}`\n\n🚀 DM mein /start karo!"
  try:
   p=await context.bot.get_user_profile_photos(user_id=m.id,limit=1)
   if p and p.photos:await update.message.reply_photo(photo=p.photos[0][-1].file_id,caption=txt,parse_mode="Markdown")
   else:await update.message.reply_text(txt,parse_mode="Markdown")
  except:await update.message.reply_text(txt,parse_mode="Markdown")
async def view_products(update,context):
 if is_banned(update.effective_user.id):return
 avail=sum(1 for v in ff_stock.values() if v["status"]=="Available")
 txt=f"🛒 *PRODUCTS:*\n🔥 *Accounts in stock:* `{avail}`\n\n"
 for pid,p in products.items():
  ok=all(materials.get(mid,{}).get("stock",0)>=q for mid,q in p.get("materials",{}).items())
  txt+=f"🔹 *ID:* `{pid}` | *{p['name']}*\n💰 *Price:* ₹{p['price']}\n"
  for mid,q in p.get("materials",{}).items():
   mn=materials.get(mid,{}).get("name","?");ms=materials.get(mid,{}).get("stock",0)
   txt+=f"  - {mn}: `{q}` (Stock: {ms})\n"
  txt+=f"📊 {'✅ IN STOCK' if ok else '❌ OUT OF STOCK'}\n{'─'*20}\n\n"
 txt+="🛍️ Buy: `/buy [ID] [coupon_optional]`"
 await update.message.reply_text(txt,parse_mode="Markdown")
async def buy_product(update,context):
 user=update.effective_user
 if is_banned(user.id):await update.message.reply_text("🚫 Banned!");return
 if not context.args:await update.message.reply_text("⚠️ Format: `/buy [id] [coupon]`",parse_mode="Markdown");return
 pid=context.args[0]
 if pid not in products:await update.message.reply_text("❌ Galat ID.");return
 if not any(v["status"]=="Available" for v in ff_stock.values()):await update.message.reply_text("❌ Stock empty!");return
 p=products[pid]
 if not all(materials.get(mid,{}).get("stock",0)>=q for mid,q in p.get("materials",{}).items()):
  await update.message.reply_text("❌ Out of stock!");return
 price=float(p["price"])
 if user.id in vip_users:price=price*0.85;coupon_txt="👑 VIP 15% discount applied!"
 else:coupon_txt="None"
 if len(context.args)>1:
  code=context.args[1].upper()
  if code in coupons:disc=coupons[code];price=price-(price*(disc/100));coupon_txt=f"{code} ({disc}% OFF)"
 global order_counter
 order_counter+=1;oid=order_counter
 pending_orders[oid]={"user_id":user.id,"product_id":pid,"final_price":price,"status":"Pending","time":datetime.now().strftime("%d/%m/%Y %H:%M")}
 context.user_data["pending_receipt_order"]=oid
 txt=(f"🛍️ *ORDER INVOICE #{oid}*\n\n📦 *Product:* {p['name']}\n"
  f"🎟️ *Coupon:* {coupon_txt}\n💰 *Amount:* ₹{price:.0f}\n\n"
  f"💳 *UPI ID:* `{UPI_ID}`\n\n⚠️ Payment karke screenshot bhejo!")
 await update.message.reply_text(txt,parse_mode="Markdown")
async def wallet_cmd(update,context):
 uid=update.effective_user.id
 bal=user_balances.get(uid,0.0)
 refs=sum(1 for v in referred_by.values() if v==uid)
 orders_done=sum(1 for o in pending_orders.values() if o["user_id"]==uid and o["status"]=="Completed")
 vip="👑 VIP Member\n" if uid in vip_users else ""
 await update.message.reply_text(f"💰 *WALLET*\n{vip}\n💵 Balance: ₹{bal:.2f}\n👥 Referrals: {refs}\n📦 Orders: {orders_done}",parse_mode="Markdown")
async def ticket_cmd(update,context):
 if is_banned(update.effective_user.id):return
 if not context.args:await update.message.reply_text("⚠️ Format: `/ticket [message]`",parse_mode="Markdown");return
 global ticket_counter
 ticket_counter+=1;tid=ticket_counter
 msg=" ".join(context.args)
 uid=update.effective_user.id
 support_tickets[tid]={"user_id":uid,"msg":msg,"status":"Open","time":datetime.now().strftime("%d/%m/%Y %H:%M")}
 await update.message.reply_text(f"✅ *Ticket #{tid} submit ho gaya!*\nAdmin jald reply karega.\nMessage: {msg}",parse_mode="Markdown")
 for aid in ADMIN_IDS:
  try:
   kb=[[InlineKeyboardButton("✅ Close",callback_data=f"closeticket_{tid}"),InlineKeyboardButton("💬 Reply",callback_data=f"replyticket_{tid}_{uid}")]]
   await context.bot.send_message(aid,f"🎫 *New Ticket #{tid}*\n👤 User: `{uid}`\n📝 {msg}\n🕐 {support_tickets[tid]['time']}",parse_mode="Markdown",reply_markup=InlineKeyboardMarkup(kb))
  except:pass
async def affiliate_panel(update,context):
 user=update.effective_user
 bot_info=await context.bot.get_me()
 link=f"https://t.me/{bot_info.username}?start=ref_{user.id}"
 bal=user_balances.get(user.id,0.0)
 refs=sum(1 for v in referred_by.values() if v==user.id)
 await update.message.reply_text(f"🤝 *AFFILIATE PANEL*\n\n👥 Referrals: `{refs}`\n💰 Earnings: ₹{bal:.2f}\n\n🔗 *Your Link:*\n{link}\n\nℹ️ Har purchase pe 10% commission!",parse_mode="Markdown")
async def reseller_panel(update,context):
 uid=update.effective_user.id
 await update.message.reply_text(f"🔄 *RESELLER PANEL*\n\n🔌 API Endpoint: `https://api.autoshop.internal/v1/fetch`\n🔑 Your Key: `KEY_{uid}X77`\n\n💡 Is key se apna store connect karo!",parse_mode="Markdown")
async def payment_info(update,context):
 await update.message.reply_text(f"💳 *UPI ID:* `{UPI_ID}`\n\n/buy se order karo phir screenshot bhejo.",parse_mode="Markdown")
async def handle_payment_screenshot(update,context):
 oid=context.user_data.get("pending_receipt_order")
 if not oid or oid not in pending_orders:await update.message.reply_text("⚠️ Pehle /buy karo!");return
 user=update.effective_user
 photo=update.message.photo[-1].file_id
 od=pending_orders[oid];pname=products[od["product_id"]]["name"]
 kb=[[InlineKeyboardButton("✅ Approve",callback_data=f"approve_{oid}"),InlineKeyboardButton("❌ Decline",callback_data=f"decline_{oid}")]]
 txt=(f"💰 *NEW PAYMENT*\n\n🆔 Order: `{oid}`\n👤 {user.first_name} (`{user.id}`)\n📦 {pname}\n💵 ₹{od['final_price']:.0f}")
 context.user_data["pending_receipt_order"]=None
 for aid in ADMIN_IDS:
  try:await context.bot.send_photo(aid,photo=photo,caption=txt,reply_markup=InlineKeyboardMarkup(kb),parse_mode="Markdown")
  except:pass
 await update.message.reply_text("✅ *Screenshot admin ko bhej diya!* Verify hone par aapko account milega.")
async def handle_buttons(update,context):
 q=update.callback_query;await q.answer()
 uid=q.from_user.id
 data=q.data
 if data.startswith("approve_") or data.startswith("decline_"):
  if not is_admin(uid):return
  action,oid_str=data.split("_",1);oid=int(oid_str)
  if oid not in pending_orders:await q.edit_message_caption(caption="❌ Order not found.");return
  od=pending_orders[oid];cuid=od["user_id"];pid=od["product_id"];price=od["final_price"]
  if action=="approve":
   akey=next((k for k,v in ff_stock.items() if v["status"]=="Available"),None)
   if not akey:await q.edit_message_caption(caption=q.message.caption+"\n\n❌ Stock empty!");return
   ok=all(materials.get(mid,{}).get("stock",0)>=q2 for mid,q2 in products[pid]["materials"].items())
   if not ok:await q.edit_message_caption(caption=q.message.caption+"\n\n❌ Material shortage!");return
   for mid,qty in products[pid]["materials"].items():materials[mid]["stock"]-=qty
   ff_stock[akey]["status"]="Sold";ff_stock[akey]["assigned_to"]=cuid
   acc=ff_stock[akey];od["status"]="Completed"
   if cuid in referred_by:
    rid=referred_by[cuid];comm=price*0.10;user_balances[rid]=user_balances.get(rid,0)+comm
    try:await context.bot.send_message(rid,f"💰 *Commission!* Referral ne buy kiya. +₹{comm:.2f} mila!",parse_mode="Markdown")
    except:pass
   try:
    await context.bot.send_message(cuid,f"🎉 *APPROVED!*\n\n🆔 Order: `{oid}`\n📦 {products[pid]['name']}\n\n🔐 *Account:*\n👤 ID: `{acc['username']}`\n🔑 Pass: `{acc['password']}`\n\n⚠️ Details badal lein!",parse_mode="Markdown")
    await q.edit_message_caption(caption=q.message.caption+f"\n\n🟢 *Approved!*\nAccount: `{acc['username']}`")
   except Exception as e:logger.error(e)
  elif action=="decline":
   od["status"]="Failed"
   try:
    await context.bot.send_message(cuid,f"❌ *Payment Rejected!*\nOrder `{oid}` fail.\nSupport: {ADMIN_USERNAME}",parse_mode="Markdown")
    await q.edit_message_caption(caption=q.message.caption+"\n\n🔴 *Declined.*")
   except:pass
 elif data.startswith("vieworders_"):
  if not is_admin(uid):return
  ft=data.split("_",1)[1]
  txt=f"📋 *ORDERS ({ft.upper()}):*\n\n"
  found=False
  for oid,od in pending_orders.items():
   if ft!="all" and od["status"].lower()!=ft:continue
   found=True;pname=products.get(od["product_id"],{}).get("name","?")
   txt+=f"🆔 `#{oid}` | `{od['user_id']}` | {pname} | ₹{od['final_price']:.0f} | {od['status']}\n"
  if not found:txt+="No records."
  await q.message.reply_text(txt,parse_mode="Markdown")
 elif data.startswith("restockselect_"):
  if not is_admin(uid):return
  mid=data.split("_",1)[1];context.user_data["admin_action"]="WAITING_FOR_RESTOCK";context.user_data["target_material_id"]=mid
  await q.message.reply_text(f"📝 *{materials[mid]['name']}* kitna add karna hai? (number bhejo)")
 elif data.startswith("closeticket_"):
  if not is_admin(uid):return
  tid=int(data.split("_")[1])
  if tid in support_tickets:
   support_tickets[tid]["status"]="Closed"
   cuid=support_tickets[tid]["user_id"]
   try:await context.bot.send_message(cuid,f"✅ *Ticket #{tid} close ho gaya!*\nSupport: {ADMIN_USERNAME}",parse_mode="Markdown")
   except:pass
   await q.edit_message_text(f"✅ Ticket #{tid} closed.")
 elif data.startswith("replyticket_"):
  if not is_admin(uid):return
  parts=data.split("_");tid=int(parts[1]);cuid=int(parts[2])
  context.user_data["admin_action"]="REPLY_TICKET";context.user_data["ticket_reply_data"]=(tid,cuid)
  await q.message.reply_text(f"💬 Ticket #{tid} ka reply likho:")
async def admin_panel(update,context):
 if not is_admin(update.effective_user.id):return
 avail=sum(1 for v in ff_stock.values() if v["status"]=="Available")
 sold=sum(1 for v in ff_stock.values() if v["status"]=="Sold")
 open_tickets=sum(1 for t in support_tickets.values() if t["status"]=="Open")
 txt=(f"⚙️ *ADMIN PANEL*\n\n"
  f"👥 Users: `{len(registered_users)}`\n🚫 Banned: `{len(ban_list)}`\n"
  f"👑 VIP: `{len(vip_users)}`\n📦 Orders: `{len(pending_orders)}`\n"
  f"✅ Stock: `{avail}` | Sold: `{sold}`\n🎫 Open Tickets: `{open_tickets}`\n\n"
  f"*Commands:*\n/add_product [id] [name] [price]\n/add_stock - Bulk upload\n"
  f"/restock - Material restock\n/create_coupon [code] [%]\n"
  f"/ban [user_id] - Ban user\n/unban [user_id] - Unban\n"
  f"/addvip [user_id] - VIP do\n/removevip [user_id] - VIP hatao\n"
  f"/orders - Orders dashboard\n/tickets - Tickets dekho\n"
  f"/broadcast [msg] - Sab ko bhejo\n/setupi [upi_id] - UPI change")
 await update.message.reply_text(txt,parse_mode="Markdown")
async def view_orders(update,context):
 if not is_admin(update.effective_user.id):return
 kb=[[InlineKeyboardButton("All",callback_data="vieworders_all"),InlineKeyboardButton("Pending",callback_data="vieworders_pending")],[InlineKeyboardButton("Completed",callback_data="vieworders_completed"),InlineKeyboardButton("Failed",callback_data="vieworders_failed")]]
 await update.message.reply_text("📋 *Orders Filter:*",reply_markup=InlineKeyboardMarkup(kb),parse_mode="Markdown")
async def view_tickets(update,context):
 if not is_admin(update.effective_user.id):return
 if not support_tickets:await update.message.reply_text("🎫 Koi ticket nahi.");return
 txt="🎫 *ALL TICKETS:*\n\n"
 for tid,t in support_tickets.items():txt+=f"#{tid} | `{t['user_id']}` | {t['status']} | {t['msg'][:30]}\n"
 await update.message.reply_text(txt,parse_mode="Markdown")
async def ban_user(update,context):
 if not is_admin(update.effective_user.id):return
 if not context.args:await update.message.reply_text("Format: /ban [user_id]");return
 try:bid=int(context.args[0]);ban_list.add(bid);await update.message.reply_text(f"🚫 User `{bid}` banned!",parse_mode="Markdown")
 except:await update.message.reply_text("❌ Galat ID.")
async def unban_user(update,context):
 if not is_admin(update.effective_user.id):return
 if not context.args:return
 try:bid=int(context.args[0]);ban_list.discard(bid);await update.message.reply_text(f"✅ User `{bid}` unbanned!",parse_mode="Markdown")
 except:pass
async def add_vip(update,context):
 if not is_admin(update.effective_user.id):return
 if not context.args:return
 try:vid=int(context.args[0]);vip_users.add(vid);await update.message.reply_text(f"👑 User `{vid}` ko VIP mila!",parse_mode="Markdown")
 except:pass
async def remove_vip(update,context):
 if not is_admin(update.effective_user.id):return
 if not context.args:return
 try:vid=int(context.args[0]);vip_users.discard(vid);await update.message.reply_text(f"✅ VIP remove ho gaya.",parse_mode="Markdown")
 except:pass
async def set_upi(update,context):
 global UPI_ID
 if not is_admin(update.effective_user.id):return
 if not context.args:return
 UPI_ID=" ".join(context.args);await update.message.reply_text(f"✅ UPI updated: `{UPI_ID}`",parse_mode="Markdown")
async def create_coupon(update,context):
 if not is_admin(update.effective_user.id):return
 if len(context.args)<2:await update.message.reply_text("Format: /create_coupon [CODE] [%]");return
 code=context.args[0].upper()
 try:disc=int(context.args[1]);coupons[code]=disc;await update.message.reply_text(f"✅ Coupon `{code}` - {disc}% OFF!",parse_mode="Markdown")
 except:await update.message.reply_text("❌ Number daalo.")
async def add_product(update,context):
 if not is_admin(update.effective_user.id):return
 if len(context.args)<3:await update.message.reply_text("Format: /add_product [id] [name] [price]");return
 pid=context.args[0];price=context.args[-1];name=" ".join(context.args[1:-1])
 products[pid]={"name":name,"price":price,"materials":{}};await update.message.reply_text(f"✅ Product *{name}* (ID:`{pid}`) add ho gaya!",parse_mode="Markdown")
async def add_stock_trigger(update,context):
 if not is_admin(update.effective_user.id):return
 context.user_data["admin_action"]="WAITING_FOR_BULK_STOCK"
 await update.message.reply_text("📝 *Bulk Upload*\nFormat:\n`id1:password1`\n`id2:password2`",parse_mode="Markdown")
async def restock_inventory(update,context):
 if not is_admin(update.effective_user.id):return
 kb=[[InlineKeyboardButton(f"{v['name']} (Stock:{v['stock']})",callback_data=f"restockselect_{k}")] for k,v in materials.items()]
 await update.message.reply_text("🔄 *Restock:*",reply_markup=InlineKeyboardMarkup(kb),parse_mode="Markdown")
async def broadcast(update,context):
 if not is_admin(update.effective_user.id):return
 msg=" ".join(context.args)
 if not msg:return
 s=f=0
 for uid in list(registered_users):
  try:await context.bot.send_message(uid,f"📢 *Announcement:*\n\n{msg}",parse_mode="Markdown");s+=1
  except:f+=1
 await update.message.reply_text(f"✅ Sent:{s} Failed:{f}")
async def handle_text(update,context):
 if is_banned(update.effective_user.id):return
 action=context.user_data.get("admin_action")
 if not action:return
 uid=update.effective_user.id
 if action=="WAITING_FOR_BULK_STOCK" and is_admin(uid):
  global stock_counter
  lines=update.message.text.split("\n");added=0
  for line in lines:
   if ":" in line:
    parts=line.split(":",1)
    if parts[0].strip() and parts[1].strip():
     stock_counter+=1;ff_stock[stock_counter]={"username":parts[0].strip(),"password":parts[1].strip(),"status":"Available","assigned_to":None};added+=1
  context.user_data["admin_action"]=None
  await update.message.reply_text(f"✅ `{added}` accounts add ho gaye!",parse_mode="Markdown")
 elif action=="WAITING_FOR_RESTOCK" and is_admin(uid):
  mid=context.user_data.get("target_material_id")
  try:
   val=float(update.message.text)
   materials[mid]["stock"]+=val;context.user_data["admin_action"]=None
   await update.message.reply_text(f"✅ *{materials[mid]['name']}* stock: `{materials[mid]['stock']}`",parse_mode="Markdown")
  except:await update.message.reply_text("❌ Number daalo!")
 elif action=="REPLY_TICKET" and is_admin(uid):
  tid,cuid=context.user_data.get("ticket_reply_data",(None,None))
  if tid and cuid:
   reply_msg=update.message.text
   try:
    await context.bot.send_message(cuid,f"💬 *Ticket #{tid} - Admin Reply:*\n\n{reply_msg}\n\nSupport: {ADMIN_USERNAME}",parse_mode="Markdown")
    await update.message.reply_text(f"✅ Reply bhej diya!")
   except:await update.message.reply_text("❌ User tak message nahi pahuncha.")
  context.user_data["admin_action"]=None
def main():
 app=Application.builder().token(BOT_TOKEN).build()
 app.add_handler(CommandHandler("start",start))
 app.add_handler(CommandHandler("products",view_products))
 app.add_handler(CommandHandler("buy",buy_product))
 app.add_handler(CommandHandler("wallet",wallet_cmd))
 app.add_handler(CommandHandler("ticket",ticket_cmd))
 app.add_handler(CommandHandler("affiliate",affiliate_panel))
 app.add_handler(CommandHandler("reseller",reseller_panel))
 app.add_handler(CommandHandler("payment",payment_info))
 app.add_handler(CommandHandler("admin",admin_panel))
 app.add_handler(CommandHandler("add_product",add_product))
 app.add_handler(CommandHandler("add_stock",add_stock_trigger))
 app.add_handler(CommandHandler("restock",restock_inventory))
 app.add_handler(CommandHandler("create_coupon",create_coupon))
 app.add_handler(CommandHandler("orders",view_orders))
 app.add_handler(CommandHandler("tickets",view_tickets))
 app.add_handler(CommandHandler("ban",ban_user))
 app.add_handler(CommandHandler("unban",unban_user))
 app.add_handler(CommandHandler("addvip",add_vip))
 app.add_handler(CommandHandler("removevip",remove_vip))
 app.add_handler(CommandHandler("setupi",set_upi))
 app.add_handler(CommandHandler("broadcast",broadcast))
 app.add_handler(MessageHandler(filters.PHOTO,handle_payment_screenshot))
 app.add_handler(MessageHandler(filters.TEXT&~filters.COMMAND,handle_text))
 app.add_handler(CallbackQueryHandler(handle_buttons))
 app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS,new_member_welcome))
 print(f"✅ Bot Online!\nAdmin: {ADMIN_USERNAME}")
 app.run_polling()
if __name__=="__main__":main()

