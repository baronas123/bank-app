import asyncio
import logging
import os

from ocpp.routing import on
from ocpp.v16 import ChargePoint as CP
from ocpp.v16 import call_result
from ocpp.v16.enums import RegistrationStatus
from websockets.server import serve

from .database import SessionLocal
from .models import ChargingSession, User

logging.basicConfig(level=logging.INFO)

PRICE_PER_KWH = float(os.getenv("PRICE_PER_KWH", "0.2"))


class ChargePoint(CP):
    async def send_boot_notification(self):
        request = call_result.BootNotification(
            current_time="2025-01-01T00:00:00Z", interval=10, status=RegistrationStatus.accepted
        )
        await self.call(request)

    @on("StartTransaction")
    async def on_start(self, connector_id, id_tag, timestamp, meter_start, reservation_id=None, **kwargs):
        db = SessionLocal()
        user = db.query(User).filter(User.username == id_tag).first()
        if not user or user.balance <= 0:
            return call_result.StartTransactionPayload(id_tag_info={"status": "Invalid"}, transaction_id=0)
        session = ChargingSession(user_id=user.id)
        db.add(session)
        db.commit()
        db.refresh(session)
        db.close()
        return call_result.StartTransactionPayload(
            transaction_id=session.id, id_tag_info={"status": "Accepted"}
        )

    @on("StopTransaction")
    async def on_stop(self, meter_stop, timestamp, transaction_id, id_tag, **kwargs):
        db = SessionLocal()
        session = db.query(ChargingSession).filter(ChargingSession.id == transaction_id).first()
        user = session.user
        energy = (meter_stop - (session.energy or 0)) / 1000
        cost = energy * PRICE_PER_KWH
        if user.balance < cost:
            session.active = False
            db.commit()
            db.close()
            return call_result.StopTransactionPayload(id_tag_info={"status": "Expired"})
        user.balance -= cost
        session.energy = energy
        session.active = False
        db.commit()
        db.close()
        return call_result.StopTransactionPayload(id_tag_info={"status": "Accepted"})


async def main():
    async def handler(websocket):
        cp = ChargePoint('CP001', websocket)
        await cp.start()
    async with serve(handler, "0.0.0.0", 9000):
        await asyncio.Future()


if __name__ == "__main__":
    asyncio.run(main())
