"""Phase 7: ads (rotation, print counts, calendar, lights, discount), coupons, raffle, templates."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select

from caspian_parking.core.coupons import is_coupon_code
from caspian_parking.core.jalali import JalaliDate, local_to_utc
from caspian_parking.core.plate import parse_plate
from caspian_parking.core.subscriptions import Light
from caspian_parking.data.models import AdPrint, CouponRedemption, Payment, RaffleDraw, WalletTransaction
from caspian_parking.data.repositories.system import AuditRepository
from caspian_parking.services import auth, templates
from caspian_parking.services.ads import AdError, AdService, ad_light
from caspian_parking.services.auth import CurrentUser
from caspian_parking.services.coupons import CouponError, CouponService, CouponStatus
from caspian_parking.services.gate_service import GateError, GateService, PaymentMethod
from caspian_parking.services.people import PeopleService, PersonInput
from caspian_parking.services.settings import set_setting

MON = date(2026, 9, 28)  # 1405/07/06


def at(day: date, hh: int, mm: int = 0) -> datetime:
    return local_to_utc(datetime.combine(day, time(hh, mm)))


@pytest.fixture
def ctx(app_ctx, clock):
    clock.set(at(MON, 10))
    with app_ctx.uow() as session:
        user = auth.create_user(session, "boss", "Boss", "secret1", preset="admin")
        set_setting(session, "coupons.unit_price", 150_000)
    app_ctx.user = CurrentUser.from_user(user)
    return app_ctx


@pytest.fixture
def shop(ctx):
    return PeopleService(ctx).create_shop("مبلمان آرتا", unit="همکف ۱۲")


# ---------------------------------------------------------------- ads


def test_create_ad_uses_package_price_and_audits(ctx, shop):
    ads = AdService(ctx)
    ad = ads.create_ad(shop.id, "silver", MON, MON + timedelta(days=30), offer="۲۰٪ تخفیف")
    assert ad.price == 12_000_000
    with ctx.read() as session:
        assert AuditRepository(session).for_record("ads", ad.id)
    with pytest.raises(AdError, match="bad_package"):
        ads.create_ad(shop.id, "platinum", MON, MON)
    with pytest.raises(AdError, match="bad_range"):
        ads.create_ad(shop.id, "gold", MON, MON - timedelta(days=1))
    with pytest.raises(AdError, match="logo_not_in_package"):
        ads.create_ad(shop.id, "bronze", MON, MON, logo_source=ctx.data_root.root / "x.png")


def test_active_ads_respect_dates_weekdays_and_placement(ctx, shop):
    ads = AdService(ctx)
    every_day = ads.create_ad(shop.id, "bronze", MON, MON + timedelta(days=6), text="a")
    weekend = ads.create_ad(shop.id, "bronze", MON, MON + timedelta(days=6), text="b", weekdays=[3, 4])
    exit_only = ads.create_ad(shop.id, "bronze", MON, MON, text="c", on_entry=False, on_exit=True)
    assert {a.id for a in ads.active_ads(MON, "entry")} == {every_day.id}
    assert {a.id for a in ads.active_ads(MON, "exit")} == {exit_only.id}
    thursday = MON + timedelta(days=3)
    assert {a.id for a in ads.active_ads(thursday, "entry")} == {every_day.id, weekend.id}
    assert ads.active_ads(MON + timedelta(days=7)) == []  # expired contracts drop out automatically
    ads.end_ad(every_day.id, "قرارداد لغو شد")
    assert ads.active_ads(MON, "entry") == []


def test_rotation_is_fair_and_prints_are_counted(ctx, shop):
    ads = AdService(ctx)
    created = [ads.create_ad(shop.id, "bronze", MON, MON, text=str(i)) for i in range(3)]
    shown = []
    for _ in range(9):
        ad = ads.next_ad("entry")
        shown.append(ad.id)
        ads.record_print(ad, "entry")
    assert shown[:3] == [a.id for a in created]
    assert all(shown.count(a.id) == 3 for a in created)
    assert ads.print_counts() == {a.id: 3 for a in created}
    with ctx.read() as session:
        assert session.scalar(select(func.count()).select_from(AdPrint)) == 9
    assert ads.next_ad("exit") is None


def test_receipt_ad_logo_only_for_premium(ctx, shop, tmp_path):
    logo = tmp_path / "shop.png"
    logo.write_bytes(b"png")
    ads = AdService(ctx)
    gold = ads.create_ad(shop.id, "gold", MON, MON, offer="هدیه", location="طبقه اول", logo_source=logo, large=True)
    receipt = ads.receipt_ad(gold)
    assert receipt.logo_path == ctx.data_root.ads / "logos" / "shop.png"
    assert receipt.logo_path.read_bytes() == b"png"
    assert (receipt.shop_name, receipt.location, receipt.offer, receipt.large) == (
        "مبلمان آرتا",
        "طبقه اول",
        "هدیه",
        True,
    )
    bronze = ads.receipt_ad(ads.create_ad(shop.id, "bronze", MON, MON, text="متن تبلیغ"))
    assert bronze.logo_path is None
    assert bronze.location == "همکف ۱۲"
    assert bronze.offer == "متن تبلیغ"


def test_contract_lights(ctx, shop):
    ad = AdService(ctx).create_ad(shop.id, "bronze", MON, MON + timedelta(days=2))
    assert ad_light(ad, at(MON, 12)) is Light.AMBER
    assert ad_light(ad, at(MON - timedelta(days=30), 12)) is Light.GREEN
    assert ad_light(ad, at(MON + timedelta(days=3), 12)) in (Light.RED, Light.BLACK)


def test_calendar_counts_slots_weekends_and_occasions(ctx, shop):
    ads = AdService(ctx)
    ads.create_ad(shop.id, "bronze", MON, MON + timedelta(days=1))
    ads.create_ad(shop.id, "bronze", MON, MON)
    days = ads.calendar(1405, 7)
    assert len(days) == 30
    by_day = {d.day: d for d in days}
    assert (by_day[MON].sold, by_day[MON].free) == (2, 1)
    assert by_day[MON + timedelta(days=1)].sold == 1
    assert by_day[MON + timedelta(days=3)].weekend  # Thursday
    nowruz = ads.calendar(1406, 1)
    assert nowruz[0].occasion == "نوروز"
    assert nowruz[0].day == JalaliDate(1406, 1, 1).to_gregorian()
    assert ads.calendar(1405, 9)[29].occasion == "شب یلدا"


def test_advertiser_subscription_discount(ctx, shop):
    people = PeopleService(ctx)
    member = people.create_person(
        PersonInput(first_name="علی", shop_id=shop.id, plates=[(parse_plate("12ب345-22"), None)])
    )
    assert people.preview_payment(member.id).amount == 4_000_000
    AdService(ctx).create_ad(shop.id, "bronze", MON, MON + timedelta(days=30))
    assert people.preview_payment(member.id).amount == 3_600_000  # 10 % default
    other = people.create_person(PersonInput(first_name="رضا", plates=[(parse_plate("55ج777-11"), None)]))
    assert people.preview_payment(other.id).amount == 4_000_000


def test_ads_need_permission(ctx, shop):
    ctx.user = None
    with pytest.raises(AdError, match="permission"):
        AdService(ctx).create_ad(shop.id, "bronze", MON, MON)
    with pytest.raises(CouponError, match="permission"):
        CouponService(ctx).sell_batch(shop.id, 5, "cash")


# ---------------------------------------------------------------- coupons


def test_sell_coupons_cash_and_wallet(ctx, shop):
    coupons = CouponService(ctx)
    batch, codes = coupons.sell_batch(shop.id, 5, "cash")
    assert batch.total == 750_000
    assert len({c.code for c in codes}) == 5
    assert all(is_coupon_code(c.code) for c in codes)
    assert codes[0].expires_on == MON + timedelta(days=30)
    with ctx.read() as session:
        payment = session.scalar(select(Payment).where(Payment.purpose == "coupon"))
        assert payment.amount == 750_000
    with pytest.raises(CouponError, match="low_balance"):
        coupons.sell_batch(shop.id, 2, "wallet")
    PeopleService(ctx).deposit(shop.id, 1_000_000, "cash")
    coupons.sell_batch(shop.id, 2, "wallet")
    assert PeopleService(ctx).balance(shop.id) == 700_000
    with ctx.read() as session:
        assert session.scalar(select(WalletTransaction.amount).where(WalletTransaction.kind == "coupon")) == -300_000
    with pytest.raises(CouponError, match="bad_quantity"):
        coupons.sell_batch(shop.id, 0, "cash")
    with pytest.raises(CouponError, match="method"):
        coupons.sell_batch(shop.id, 1, "bitcoin")


def test_coupon_zeroes_fee_but_not_night_fine_and_is_single_use(ctx, shop, clock):
    _batch, codes = CouponService(ctx).sell_batch(shop.id, 2, "cash")
    gate = GateService(ctx)
    entry = gate.register_entry(parse_plate("12ب345-22"))
    clock.advance(minutes=90)
    plain = gate.quote(entry.session.id)
    assert plain.amount_due > 0
    with_coupon = gate.quote(entry.session.id, coupon_code=codes[0].code)
    assert with_coupon.amount_due == 0
    assert with_coupon.breakdown.coupon_discount == plain.amount_due
    visit = gate.complete_exit(with_coupon, None)
    assert visit.amount_paid == 0
    with ctx.read() as session:
        assert session.scalar(select(CouponRedemption.discount)) == plain.amount_due
    # single use
    again = gate.register_entry(parse_plate("55ج777-11"))
    with pytest.raises(GateError, match=r"coupons.used"):
        gate.quote(again.session.id, coupon_code=codes[0].code)
    # overnight: the fee is zeroed, the night fine stays
    clock.set(at(MON + timedelta(days=1), 10))
    night = gate.quote(again.session.id, coupon_code=codes[1].code)
    assert night.breakdown.night_fines > 0
    assert night.amount_due == night.breakdown.night_fines
    gate.complete_exit(night, PaymentMethod.CASH)


def test_coupon_invalid_and_expired(ctx, shop, clock):
    coupons = CouponService(ctx)
    _batch, codes = coupons.sell_batch(shop.id, 1, "cash")
    with pytest.raises(CouponError, match="invalid"):
        coupons.check("123")
    with pytest.raises(CouponError, match="invalid"):
        coupons.check("900000000008")  # valid shape, unknown code
    assert coupons.check(codes[0].code).id == codes[0].id
    clock.advance(days=31)
    with pytest.raises(CouponError, match="expired"):
        coupons.check(codes[0].code)
    assert coupons.coupons_of(codes[0].batch_id)[0][1] is CouponStatus.EXPIRED


def test_same_coupon_redeemed_twice_in_parallel_quotes(ctx, shop, clock):
    _batch, codes = CouponService(ctx).sell_batch(shop.id, 1, "cash")
    gate = GateService(ctx)
    first = gate.register_entry(parse_plate("12ب345-22"))
    second = gate.register_entry(parse_plate("55ج777-11"))
    clock.advance(minutes=90)
    q1 = gate.quote(first.session.id, coupon_code=codes[0].code)
    q2 = gate.quote(second.session.id, coupon_code=codes[0].code)
    gate.complete_exit(q1, None)
    with pytest.raises(GateError, match=r"coupons.used"):
        gate.complete_exit(q2, None)
    assert gate.get_active(second.session.id) is not None


def test_coupon_stats(ctx, shop, clock):
    coupons = CouponService(ctx)
    _batch, codes = coupons.sell_batch(shop.id, 3, "cash")
    gate = GateService(ctx)
    entry = gate.register_entry(parse_plate("12ب345-22"))
    clock.advance(minutes=90)
    gate.complete_exit(gate.quote(entry.session.id, coupon_code=codes[0].code), None)
    clock.advance(days=31)
    [stats] = coupons.stats()
    assert (stats.bought, stats.used, stats.expired, stats.open, stats.revenue) == (3, 1, 2, 0, 450_000)


# ---------------------------------------------------------------- raffle


def test_monthly_raffle(ctx, shop, clock):
    ads = AdService(ctx)
    with pytest.raises(AdError, match="no_candidates"):
        ads.draw_raffle(1405, 7)
    gate = GateService(ctx)
    tickets = []
    for plate in ("12ب345-22", "55ج777-11", "31د456-44"):
        tickets.append(gate.register_entry(parse_plate(plate)).session.ticket_no)
        clock.advance(minutes=5)
    wrong = gate.register_entry(parse_plate("44د111-55"))
    gate.cancel_entry(wrong.session.id, "ثبت اشتباه")  # cancelled receipts do not take part
    draw = ads.draw_raffle(1405, 7, sponsor_shop_id=shop.id)
    assert draw.winner_ticket in tickets
    assert draw.candidates == 3
    assert len(draw.candidates_digest) == 64
    with pytest.raises(AdError, match="already_drawn"):
        ads.draw_raffle(1405, 7)
    with ctx.read() as session:
        assert session.scalar(select(func.count()).select_from(RaffleDraw)) == 1
    assert [d.month for d in ads.raffles()] == ["1405-07"]


# ---------------------------------------------------------------- templates


def test_templates_listing_selection_and_fallback(ctx):
    folder = ctx.data_root.templates
    (folder / "مناسبت‌ها").mkdir()
    (folder / "مناسبت‌ها" / "یلدا.png").write_bytes(b"x")
    (folder / "ساده.jpg").write_bytes(b"x")
    (folder / "notes.txt").write_text("skip")
    found = templates.list_templates(folder)
    assert [(t.category, t.name) for t in found] == [("", "ساده"), ("مناسبت‌ها", "یلدا")]
    assert templates.categories(found) == ["", "مناسبت‌ها"]
    assert [t.name for t in templates.search(found, "یلد")] == ["یلدا"]
    assert templates.selected_template(ctx).path is None
    templates.select_template(ctx, found[1].relative)
    selection = templates.selected_template(ctx)
    assert selection.path == found[1].path
    found[1].path.unlink()
    selection = templates.selected_template(ctx)
    assert selection.missing
    assert selection.path is None
    templates.select_template(ctx, None)
    assert not templates.selected_template(ctx).missing
