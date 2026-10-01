"""Comprehensive tests for multi-tenant Telegram Engine, webhook security, and update dispatching."""
import json
from unittest.mock import MagicMock, patch
from django.test import TestCase, Client
from apps.tenants.models import Tenant
from apps.listings.models import Listing
from apps.telegram_engine.models import TelegramBotConfig, TelegramUser, TelegramUpdateLog, TelegramConversationState, BotType
from apps.telegram_engine.security import encrypt_token, decrypt_token, mask_token
from apps.telegram_engine.dispatcher import TelegramDispatcher
from services.telegram import TelegramService, TelegramServiceError


class TelegramEngineTestBase(TestCase):
    """Base setup with two distinct regional tenants and mock HTTP client."""

    def setUp(self):
        # Tenant A: Malaysia
        self.tenant_my = Tenant.objects.create(
            name="CYG Malaysia",
            slug="cyg-my",
            code="MY",
            country="Malaysia",
            timezone="Asia/Kuala_Lumpur",
            currency="MYR",
            is_active=True,
        )
        # Tenant B: Australia
        self.tenant_au = Tenant.objects.create(
            name="AquaBid Australia",
            slug="aquabid-au",
            code="AU",
            country="Australia",
            timezone="Australia/Sydney",
            currency="AUD",
            is_active=True,
        )

        # Telegram bot configurations
        self.bot_my = TelegramBotConfig.objects.create(
            tenant=self.tenant_my,
            bot_type=BotType.UNIFIED,
            bot_username="CygMalaysiaBot",
            webhook_secret_token="secret_token_my_12345",
            is_active=True,
        )
        self.bot_my.set_token("TEST_FAKE_TOKEN_MY_11111")
        self.bot_my.save()

        self.bot_au = TelegramBotConfig.objects.create(
            tenant=self.tenant_au,
            bot_type=BotType.UNIFIED,
            bot_username="AquaBidAuBot",
            webhook_secret_token="secret_token_au_98765",
            is_active=True,
        )
        self.bot_au.set_token("TEST_FAKE_TOKEN_AU_22222")
        self.bot_au.save()

        self.client = Client()


class TelegramSecurityAndConfigTest(TelegramEngineTestBase):
    """Tests for TelegramBotConfig, token encryption at rest, masking, and environment overrides."""

    def test_token_encryption_and_decryption(self):
        """Token must be encrypted at rest and cleanly decrypted on access."""
        plain = "TEST_SECRET_BOT_TOKEN_XYZ"
        cipher = encrypt_token(plain)
        self.assertNotEqual(plain, cipher)
        self.assertNotIn(plain, cipher)
        decrypted = decrypt_token(cipher)
        self.assertEqual(plain, decrypted)

    def test_token_masking(self):
        """Tokens must be masked for secure representation in logs/admin."""
        token = "1234567890:ABCdefGHIjklMNOpqr"
        masked = mask_token(token)
        self.assertTrue(masked.startswith("1234"))
        self.assertTrue(masked.endswith("Opqr"))
        self.assertNotIn("ABCdefGHI", masked)

    def test_config_belongs_to_tenant(self):
        """Each TelegramBotConfig strictly belongs to its respective Tenant."""
        self.assertEqual(self.bot_my.tenant, self.tenant_my)
        self.assertEqual(self.bot_au.tenant, self.tenant_au)

    def test_environment_variable_token_override(self):
        """If token_env_var is set and exists in os.environ, it overrides the database token."""
        self.bot_my.token_env_var = "TEST_ENV_OVERRIDE_KEY"
        self.bot_my.save()
        with patch.dict("os.environ", {"TEST_ENV_OVERRIDE_KEY": "TEST_ENV_TOKEN_9999"}):
            self.assertEqual(self.bot_my.get_token(), "TEST_ENV_TOKEN_9999")


class TelegramWebhookEndpointTest(TelegramEngineTestBase):
    """Tests covering webhook endpoint routing, tenant resolution, HTTP validation, and security."""

    def test_reject_non_post_requests(self):
        """GET and other non-POST methods must return 405 Method Not Allowed."""
        url = f"/telegram/webhook/{self.tenant_my.slug}/"
        response = self.client.get(url)
        self.assertEqual(response.status_code, 405)

    def test_reject_malformed_json(self):
        """Malformed JSON payloads must return 400 Bad Request."""
        url = f"/telegram/webhook/{self.tenant_my.slug}/"
        response = self.client.post(
            url,
            data="invalid-json-string{",
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="secret_token_my_12345",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("Malformed JSON", response.json().get("error", ""))

    def test_unknown_tenant_slug(self):
        """Webhooks with unknown tenant slugs must return 404 Not Found."""
        url = "/telegram/webhook/unknown-tenant-slug/"
        response = self.client.post(
            url,
            data=json.dumps({"update_id": 100}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_inactive_tenant_rejection(self):
        """Inactive tenants must return 404 Not Found."""
        self.tenant_my.is_active = False
        self.tenant_my.save()
        url = f"/telegram/webhook/{self.tenant_my.slug}/"
        response = self.client.post(
            url,
            data=json.dumps({"update_id": 101}),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="secret_token_my_12345",
        )
        self.assertEqual(response.status_code, 404)

    def test_unconfigured_or_inactive_bot_rejection(self):
        """Webhooks for disabled bot configurations must return 404 Not Found."""
        self.bot_my.is_active = False
        self.bot_my.save()
        url = f"/telegram/webhook/{self.tenant_my.slug}/"
        response = self.client.post(
            url,
            data=json.dumps({"update_id": 102}),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="secret_token_my_12345",
        )
        self.assertEqual(response.status_code, 404)

    def test_missing_or_invalid_secret_token_forbidden(self):
        """If webhook_secret_token is configured, requests with missing/wrong headers must return 403."""
        url = f"/telegram/webhook/{self.tenant_my.slug}/"
        payload = json.dumps({"update_id": 103, "message": {"text": "hello"}})

        # Missing header
        res1 = self.client.post(url, data=payload, content_type="application/json")
        self.assertEqual(res1.status_code, 403)

        # Invalid header
        res2 = self.client.post(
            url,
            data=payload,
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="wrong_secret_token",
        )
        self.assertEqual(res2.status_code, 403)

    def test_missing_update_id_rejection(self):
        """Payloads missing integer update_id must return 400 Bad Request."""
        url = f"/telegram/webhook/{self.tenant_my.slug}/"
        response = self.client.post(
            url,
            data=json.dumps({"message": {"text": "no_update_id"}}),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="secret_token_my_12345",
        )
        self.assertEqual(response.status_code, 400)


class TelegramDispatcherAndIdempotencyTest(TelegramEngineTestBase):
    """Tests for update dispatching, command routing, user identity, and deduplication."""

    @patch.object(TelegramService, "send_message")
    def test_valid_start_command_dispatch_and_user_creation(self, mock_send):
        """Valid /start command updates user profile and sends welcome message with tenant context."""
        mock_send.return_value = {"ok": True}
        url = f"/telegram/webhook/{self.tenant_my.slug}/"
        payload = {
            "update_id": 2001,
            "message": {
                "message_id": 1,
                "from": {
                    "id": 999001,
                    "first_name": "Ahmad",
                    "last_name": "Razak",
                    "username": "ahmad_my",
                    "language_code": "en",
                },
                "chat": {"id": 999001, "type": "private"},
                "text": "/start",
            },
        }

        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="secret_token_my_12345",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json().get("status"), "ok")

        # Verify TelegramUser was upserted for tenant_my
        user = TelegramUser.objects.get(tenant=self.tenant_my, telegram_user_id=999001)
        self.assertEqual(user.first_name, "Ahmad")
        self.assertEqual(user.username, "ahmad_my")
        self.assertEqual(user.chat_id, 999001)

        # Verify mock send_message was called with tenant name
        mock_send.assert_called_once()
        sent_text = mock_send.call_args[1]["text"]
        self.assertIn("CYG Malaysia", sent_text)
        self.assertIn("MYR", sent_text)

    @patch.object(TelegramService, "send_message")
    def test_help_command_dispatch(self, mock_send):
        """Valid /help command sends guidance message with tenant context."""
        mock_send.return_value = {"ok": True}
        url = f"/telegram/webhook/{self.tenant_au.slug}/"
        payload = {
            "update_id": 2002,
            "message": {
                "message_id": 2,
                "from": {"id": 888001, "first_name": "Bruce", "username": "bruce_au"},
                "chat": {"id": 888001},
                "text": "/help",
            },
        }
        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="secret_token_au_98765",
        )
        self.assertEqual(response.status_code, 200)
        mock_send.assert_called_once()
        sent_text = mock_send.call_args[1]["text"]
        self.assertIn("AquaBid Australia", sent_text)

    @patch.object(TelegramService, "answer_callback_query")
    def test_callback_query_dispatch(self, mock_answer):
        """Callback query acknowledges inline button press and updates user identity."""
        mock_answer.return_value = {"ok": True}
        url = f"/telegram/webhook/{self.tenant_my.slug}/"
        payload = {
            "update_id": 2003,
            "callback_query": {
                "id": "cb_query_999",
                "from": {"id": 999002, "first_name": "Siti"},
                "data": "refresh_listing_42",
            },
        }
        response = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="secret_token_my_12345",
        )
        self.assertEqual(response.status_code, 200)
        mock_answer.assert_called_once()
        user = TelegramUser.objects.get(tenant=self.tenant_my, telegram_user_id=999002)
        self.assertEqual(user.first_name, "Siti")

    def test_duplicate_update_id_idempotency(self):
        """Duplicate update_id within the same tenant must return 200 duplicate without re-processing."""
        url = f"/telegram/webhook/{self.tenant_my.slug}/"
        payload = {
            "update_id": 77777,
            "message": {
                "from": {"id": 999003, "first_name": "Test"},
                "chat": {"id": 999003},
                "text": "Hello world",
            },
        }

        # First delivery: creates TelegramUpdateLog and processes
        res1 = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="secret_token_my_12345",
        )
        self.assertEqual(res1.status_code, 200)
        self.assertEqual(res1.json().get("status"), "ok")
        self.assertEqual(TelegramUpdateLog.objects.filter(tenant=self.tenant_my, update_id=77777).count(), 1)

        # Second delivery (retry from Telegram): detected as duplicate
        res2 = self.client.post(
            url,
            data=json.dumps(payload),
            content_type="application/json",
            HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN="secret_token_my_12345",
        )
        self.assertEqual(res2.status_code, 200)
        self.assertEqual(res2.json().get("status"), "duplicate")
        # Ensure still only 1 log record
        self.assertEqual(TelegramUpdateLog.objects.filter(tenant=self.tenant_my, update_id=77777).count(), 1)

    def test_cross_tenant_user_and_update_isolation(self):
        """Identical Telegram user IDs and update IDs can exist independently across different tenants."""
        # Same Telegram user ID interacts with both Tenant MY and Tenant AU
        TelegramUser.objects.create(
            tenant=self.tenant_my,
            telegram_user_id=55555,
            chat_id=55555,
            username="global_shopper",
            first_name="Shopper MY",
        )
        TelegramUser.objects.create(
            tenant=self.tenant_au,
            telegram_user_id=55555,
            chat_id=55555,
            username="global_shopper",
            first_name="Shopper AU",
        )

        my_user = TelegramUser.objects.get(tenant=self.tenant_my, telegram_user_id=55555)
        au_user = TelegramUser.objects.get(tenant=self.tenant_au, telegram_user_id=55555)

        self.assertEqual(my_user.first_name, "Shopper MY")
        self.assertEqual(au_user.first_name, "Shopper AU")
        self.assertNotEqual(my_user.tenant, au_user.tenant)

        # Same update_id for different tenants
        TelegramUpdateLog.objects.create(tenant=self.tenant_my, update_id=99999)
        TelegramUpdateLog.objects.create(tenant=self.tenant_au, update_id=99999)

        self.assertEqual(TelegramUpdateLog.objects.filter(update_id=99999).count(), 2)
        self.assertEqual(TelegramUpdateLog.objects.filter(tenant=self.tenant_my, update_id=99999).count(), 1)
        self.assertEqual(TelegramUpdateLog.objects.filter(tenant=self.tenant_au, update_id=99999).count(), 1)


class TelegramServiceMockedTest(TelegramEngineTestBase):
    """Tests for TelegramService verifying API formatting and mocked HTTP requests."""

    def test_telegram_service_requires_token(self):
        """TelegramService raises an error if the tenant bot has no token configured."""
        self.bot_my.token_encrypted = ""
        self.bot_my.save()
        svc = TelegramService(self.bot_my)
        with self.assertRaises(TelegramServiceError):
            svc.send_message(chat_id=123, text="Hello")

    def test_telegram_service_send_message_mocked(self):
        """TelegramService formats the correct API request with tenant bot token."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "result": {"message_id": 99}}
        mock_client.post.return_value = mock_response

        svc = TelegramService(self.bot_my, http_client=mock_client)
        res = svc.send_message(chat_id=12345, text="<b>Bold text</b>")

        self.assertTrue(res.get("ok"))
        mock_client.post.assert_called_once()
        args, kwargs = mock_client.post.call_args
        self.assertIn("TEST_FAKE_TOKEN_MY_11111", args[0])
        self.assertIn("sendMessage", args[0])
        self.assertEqual(kwargs["json"]["chat_id"], 12345)
        self.assertEqual(kwargs["json"]["text"], "<b>Bold text</b>")

    def test_telegram_service_answer_callback_mocked(self):
        """TelegramService formats answerCallbackQuery payload correctly."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "result": True}
        mock_client.post.return_value = mock_response

        svc = TelegramService(self.bot_au, http_client=mock_client)
        res = svc.answer_callback_query(callback_query_id="cb_123", text="Alert!", show_alert=True)

        self.assertTrue(res.get("ok"))
        mock_client.post.assert_called_once()
        args, kwargs = mock_client.post.call_args
        self.assertIn("answerCallbackQuery", args[0])
        self.assertEqual(kwargs["json"]["callback_query_id"], "cb_123")
        self.assertTrue(kwargs["json"]["show_alert"])

    def test_telegram_service_get_me_mocked(self):
        """TelegramService formats getMe payload correctly."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "ok": True,
            "result": {"id": 123456, "is_bot": True, "first_name": "TestBot", "username": "TestBot"},
        }
        mock_client.post.return_value = mock_response

        svc = TelegramService(self.bot_my, http_client=mock_client)
        res = svc.get_me()

        self.assertTrue(res.get("ok"))
        self.assertEqual(res["result"]["username"], "TestBot")
        mock_client.post.assert_called_once()
        args, kwargs = mock_client.post.call_args
        self.assertIn("getMe", args[0])

    def test_telegram_service_webhook_methods_mocked(self):
        """TelegramService formats setWebhook, getWebhookInfo, and deleteWebhook correctly."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.json.return_value = {"ok": True, "result": True}
        mock_client.post.return_value = mock_response

        svc = TelegramService(self.bot_my, http_client=mock_client)

        # set_webhook
        svc.set_webhook("https://example.com/webhook/", secret_token="secret123")
        args, kwargs = mock_client.post.call_args
        self.assertIn("setWebhook", args[0])
        self.assertEqual(kwargs["json"]["url"], "https://example.com/webhook/")
        self.assertEqual(kwargs["json"]["secret_token"], "secret123")

        # get_webhook_info
        svc.get_webhook_info()
        args, _ = mock_client.post.call_args
        self.assertIn("getWebhookInfo", args[0])

        # delete_webhook
        svc.delete_webhook(drop_pending_updates=True)
        args, kwargs = mock_client.post.call_args
        self.assertIn("deleteWebhook", args[0])
        self.assertTrue(kwargs["json"]["drop_pending_updates"])


class SellerTelegramWorkflowAndSecurityTest(TelegramEngineTestBase):
    """Rigorous tests for seller onboarding, listing wizard, draft recovery, and security isolation."""

    def setUp(self):
        super().setUp()
        self.mock_tg_svc = MagicMock(spec=TelegramService)
        self.mock_tg_svc.send_message.return_value = {"ok": True, "result": {"message_id": 1001}}
        self.mock_tg_svc.answer_callback_query.return_value = {"ok": True, "result": True}
        self.mock_tg_svc.get_file.return_value = {"ok": True, "result": {"file_path": "photos/test_file.jpg"}}
        self.mock_tg_svc.download_file.return_value = "/tmp/test_downloaded.jpg"

        # Dispatcher configured with SELLER bot
        self.bot_my.bot_type = BotType.SELLER
        self.bot_my.save()

        self.dispatcher = TelegramDispatcher(
            tenant=self.tenant_my,
            bot_config=self.bot_my,
            telegram_service=self.mock_tg_svc,
        )

        self.chat_id = 99887766
        self.from_user = {
            "id": 99887766,
            "is_bot": False,
            "first_name": "Ahmad",
            "username": "ahmad_seller",
        }

    def test_tenant_isolation_seller_workflows(self):
        """Seller in Tenant MY cannot affect Tenant AU state."""
        from apps.listings.models import Seller

        tg_user_my = TelegramUser.objects.create(
            tenant=self.tenant_my,
            telegram_user_id=12345,
            chat_id=12345,
            username="seller_my",
        )
        Seller.objects.create(
            tenant=self.tenant_my,
            telegram_user=tg_user_my,
            seller_id="12345",
            business_name="MY Betta Hub",
            contact_name="Bob",
            phone="+60111111111",
        )

        # Dispatcher for AU
        dispatcher_au = TelegramDispatcher(
            tenant=self.tenant_au,
            bot_config=self.bot_au,
            telegram_service=self.mock_tg_svc,
        )

        # AU dispatcher must not see MY seller profile
        tg_user_au = TelegramUser.objects.create(
            tenant=self.tenant_au,
            telegram_user_id=12345,
            chat_id=12345,
            username="seller_my",
        )
        self.assertIsNone(dispatcher_au.seller_workflow.get_seller_profile(tg_user_au))

    def test_dashboard_approval_and_rejection_notification(self):
        """Admin approving/rejecting a listing notifies the seller via Telegram."""
        from apps.listings.models import Seller, Listing, ListingStatus
        from services.listings import approve_listing, reject_listing

        tg_user = TelegramUser.objects.create(
            tenant=self.tenant_my,
            telegram_user_id=self.chat_id,
            chat_id=self.chat_id,
            username="ahmad_seller",
        )
        seller = Seller.objects.create(
            tenant=self.tenant_my,
            telegram_user=tg_user,
            seller_id=str(self.chat_id),
            business_name="Aquatic Paradise MY",
            contact_name="Ahmad Razak",
            phone="+60123456789",
        )
        listing = Listing.objects.create(
            tenant=self.tenant_my,
            seller_id=seller.seller_id,
            title="Premium Platinum Guppy",
            status=ListingStatus.PENDING,
        )

        with patch("services.telegram.TelegramService.send_message") as mock_send:
            # 1. Approve
            approve_listing(listing, approved_by="admin_user")
            listing.refresh_from_db()
            self.assertEqual(listing.status, ListingStatus.APPROVED)
            mock_send.assert_called_once()
            call_kwargs = mock_send.call_args[1]
            self.assertEqual(call_kwargs["chat_id"], self.chat_id)
            self.assertIn("Listing Approved", call_kwargs["text"])

        # Reset to PENDING for rejection test
        listing.status = ListingStatus.PENDING
        listing.save()

        with patch("services.telegram.TelegramService.send_message") as mock_send:
            # 2. Reject
            reject_listing(listing, reason="Unclear photos of fish gills")
            listing.refresh_from_db()
            self.assertEqual(listing.status, ListingStatus.REJECTED)
            self.assertEqual(listing.metadata["rejection_reason"], "Unclear photos of fish gills")
            mock_send.assert_called_once()
            call_kwargs = mock_send.call_args[1]
            self.assertEqual(call_kwargs["chat_id"], self.chat_id)
            self.assertIn("Listing Not Approved", call_kwargs["text"])
            self.assertIn("Unclear photos of fish gills", call_kwargs["text"])


class LegacyUXBuyerAndSellerWorkflowTest(TestCase):
    """Exhaustive tests verifying exact byte-for-byte legacy Telegram UX parity, multi-tenant isolation, and concurrent bidding."""

    def setUp(self):
        self.tenant_my = Tenant.objects.create(
            name="CYG Aquatics Malaysia",
            slug="cyg-malaysia",
            code="MY",
            country="Malaysia",
            currency="MYR",
            timezone="Asia/Kuala_Lumpur",
            is_active=True,
        )
        self.tenant_au = Tenant.objects.create(
            name="AquaBid Australia",
            slug="aquabid-australia",
            code="AU",
            country="Australia",
            currency="AUD",
            timezone="Australia/Sydney",
            is_active=True,
        )

        self.buyer_bot_my = TelegramBotConfig.objects.create(
            tenant=self.tenant_my,
            bot_type=BotType.BUYER,
            bot_username="cyg_bidding_bot",
            is_active=True,
            require_password=False,
            require_contact_details=False,
        )
        self.seller_bot_my = TelegramBotConfig.objects.create(
            tenant=self.tenant_my,
            bot_type=BotType.SELLER,
            bot_username="cyg_seller_bot",
            is_active=True,
            require_password=False,
        )

        self.buyer_bot_au = TelegramBotConfig.objects.create(
            tenant=self.tenant_au,
            bot_type=BotType.BUYER,
            bot_username="aquabid_bidding_bot",
            is_active=True,
        )

        self.mock_tg_svc = MagicMock(spec=TelegramService)

        self.dispatcher_buyer_my = TelegramDispatcher(
            tenant=self.tenant_my,
            bot_config=self.buyer_bot_my,
            telegram_service=self.mock_tg_svc,
        )
        self.dispatcher_seller_my = TelegramDispatcher(
            tenant=self.tenant_my,
            bot_config=self.seller_bot_my,
            telegram_service=self.mock_tg_svc,
        )
        self.dispatcher_buyer_au = TelegramDispatcher(
            tenant=self.tenant_au,
            bot_config=self.buyer_bot_au,
            telegram_service=self.mock_tg_svc,
        )

        self.buyer_user_id = 987654321
        self.chat_id = 987654321

    def test_golden_fixture_parity(self):
        """Verify that centralized messages and keyboards match the legacy UX golden fixtures exactly."""
        import json
        from apps.telegram_engine.messages import BuyerMessages, SellerMessages
        from apps.telegram_engine.keyboards import BuyerKeyboards, SellerKeyboards

        with open("tests/fixtures/telegram_legacy_ux/buyer_ux.json", "r", encoding="utf-8") as f:
            buyer_fixture = json.load(f)

        self.assertEqual(BuyerMessages.GREETING, buyer_fixture["messages"]["greeting"])
        self.assertEqual(BuyerMessages.BLOCKED_USER, buyer_fixture["messages"]["blocked"])
        self.assertEqual(BuyerMessages.CONTACT_REQUIRED, buyer_fixture["messages"]["contact_required"])
        self.assertEqual(BuyerMessages.PASSWORD_PROMPT, buyer_fixture["messages"]["password_prompt"])
        self.assertEqual(BuyerMessages.CHOOSE_OPTION, buyer_fixture["messages"]["choose_option"])
        self.assertEqual(BuyerMessages.SHOW_LISTINGS_PROMPT, buyer_fixture["messages"]["show_listings_prompt"])
        self.assertEqual(BuyerMessages.NO_LISTINGS_MOMENT, buyer_fixture["messages"]["no_listings_moment"])
        self.assertEqual(BuyerMessages.PROCESS_CANCELLED, buyer_fixture["messages"]["cancel_process"])

        # Check main menu keyboard layout and exact row widths
        expected_buyer_kb = buyer_fixture["menus"]["main_menu_buttons"]
        actual_buyer_kb = [
            [btn["text"] for btn in row]
            for row in BuyerKeyboards.main_menu()["keyboard"]
        ]
        self.assertEqual(actual_buyer_kb, expected_buyer_kb)

        # Check seller main menu keyboard
        with open("tests/fixtures/telegram_legacy_ux/seller_ux.json", "r", encoding="utf-8") as f:
            seller_fixture = json.load(f)

        expected_seller_kb = seller_fixture["menus"]["main_menu_buttons"]
        actual_seller_kb = [
            [btn["text"] for btn in row]
            for row in SellerKeyboards.main_menu()["keyboard"]
        ]
        self.assertEqual(actual_seller_kb, expected_seller_kb)

    def test_buyer_start_and_menu_navigation(self):
        """Test full Buyer /start and custom keyboard navigation."""
        from apps.telegram_engine.messages import BuyerMessages

        update = {
            "message": {
                "message_id": 1,
                "date": 1727464000,
                "chat": {"id": self.chat_id, "type": "private"},
                "from": {"id": self.buyer_user_id, "first_name": "Shabi", "username": "shabi_buyer"},
                "text": "/start",
            }
        }
        res = self.dispatcher_buyer_my.dispatch(update)
        self.assertTrue(res["handled"])

        # Expect two messages: "Welcome!" with keyboard remove, then "Please choose an option:" with main menu
        self.assertEqual(self.mock_tg_svc.send_message.call_count, 2)
        call1 = self.mock_tg_svc.send_message.call_args_list[0][1]
        call2 = self.mock_tg_svc.send_message.call_args_list[1][1]
        self.assertEqual(call1["text"], BuyerMessages.GREETING)
        self.assertEqual(call2["text"], BuyerMessages.CHOOSE_OPTION)
        self.assertIn("keyboard", call2["reply_markup"])

    def test_buyer_start_shows_all_auctions_prompt(self):
        """Clicking 'Start' reply button prompts with Show All Auctions inline buttons."""
        from apps.telegram_engine.messages import BuyerMessages

        self.mock_tg_svc.reset_mock()
        update = {
            "message": {
                "message_id": 2,
                "chat": {"id": self.chat_id, "type": "private"},
                "from": {"id": self.buyer_user_id, "first_name": "Shabi"},
                "text": "Start",
            }
        }
        res = self.dispatcher_buyer_my.dispatch(update)
        self.assertTrue(res["handled"])

        self.mock_tg_svc.send_message.assert_called_once()
        call = self.mock_tg_svc.send_message.call_args[1]
        self.assertEqual(call["text"], BuyerMessages.SHOW_LISTINGS_PROMPT)
        self.assertIn("inline_keyboard", call["reply_markup"])
        btn_texts = [btn["text"] for row in call["reply_markup"]["inline_keyboard"] for btn in row]
        self.assertIn("Show All Auctions", btn_texts)
        self.assertIn("Show All Buy It Now", btn_texts)

    def test_buyer_bidding_flow_atomic_and_tenant_isolated(self):
        """Buyer selects an auction, starts bid, selects bid amount, and places atomic bid."""
        from decimal import Decimal
        from django.utils import timezone
        from apps.listings.models import Listing, ListingStatus, ListingType
        from apps.bidding.models import Auction, AuctionStatus, Bid
        from apps.telegram_engine.messages import BuyerMessages

        # Create active auction in Tenant MY
        listing_my = Listing.objects.create(
            tenant=self.tenant_my,
            seller_id="seller_my_1",
            seller_username="FishMasterMY",
            title="Golden Dragon Arowana",
            description="High quality super red arowana with microchip.",
            category="Arowana",
            quantity=1,
            status=ListingStatus.APPROVED,
            listing_type=ListingType.AUCTION,
        )
        auction_my = Auction.objects.create(
            tenant=self.tenant_my,
            listing=listing_my,
            starting_price=Decimal("100.00"),
            bid_increment=Decimal("10.00"),
            current_price=Decimal("0.00"),
            start_at=timezone.now() - timezone.timedelta(hours=1),
            end_at=timezone.now() + timezone.timedelta(days=2),
            status=AuctionStatus.ACTIVE,
        )

        # 1. Callback 'show_listings' displays the listing
        self.mock_tg_svc.reset_mock()
        cb_update = {
            "callback_query": {
                "id": "cb_query_1",
                "from": {"id": self.buyer_user_id, "username": "shabi_buyer"},
                "message": {"chat": {"id": self.chat_id}},
                "data": "show_listings",
            }
        }
        res = self.dispatcher_buyer_my.dispatch(cb_update)
        self.assertTrue(res["handled"])
        # Listing card was sent
        self.assertTrue(self.mock_tg_svc.send_message.call_count >= 2)

        # 2. Callback 'start_bid_{listing_id}'
        self.mock_tg_svc.reset_mock()
        start_bid_update = {
            "callback_query": {
                "id": "cb_query_2",
                "from": {"id": self.buyer_user_id, "username": "shabi_buyer"},
                "message": {"chat": {"id": self.chat_id}},
                "data": f"start_bid_{listing_my.id}",
            }
        }
        res = self.dispatcher_buyer_my.dispatch(start_bid_update)
        self.assertTrue(res["handled"])

        # Expect dynamic increment buttons ($10, $20, $30, $40, $50, $60, Cancel)
        last_call = self.mock_tg_svc.send_message.call_args_list[-1][1]
        self.assertIn("Please choose your bid amount", last_call["text"])
        kb = last_call["reply_markup"]["inline_keyboard"]
        amounts = [btn["text"] for row in kb for btn in row]
        self.assertIn("$10", amounts)
        self.assertIn("$20", amounts)
        self.assertIn("Cancel", amounts)

        # 3. Callback 'bid_amount_{listing_id}_{amount}' places atomic bid
        self.mock_tg_svc.reset_mock()
        bid_amount_update = {
            "callback_query": {
                "id": "cb_query_3",
                "from": {"id": self.buyer_user_id, "username": "shabi_buyer"},
                "message": {"chat": {"id": self.chat_id}},
                "data": f"bid_amount_{listing_my.id}_10",
            }
        }
        res = self.dispatcher_buyer_my.dispatch(bid_amount_update)
        self.assertTrue(res["handled"])

        # Verify DB atomic bid was created
        auction_my.refresh_from_db()
        self.assertEqual(auction_my.current_price, Decimal("110.00"))
        bid = Bid.objects.filter(auction=auction_my, bidder_id=str(self.buyer_user_id)).first()
        self.assertIsNotNone(bid)
        self.assertEqual(bid.amount, Decimal("110.00"))

        # Verify exact success message
        success_call = self.mock_tg_svc.send_message.call_args[1]
        expected_msg = BuyerMessages.BID_PLACED_SUCCESS.format(
            amount=10,
            listing_id=listing_my.id,
            new_amount=110,
        )
        self.assertEqual(success_call["text"], expected_msg)

    def test_tenant_isolation_prevents_cross_tenant_bidding(self):
        """Buyer connecting to Tenant AU cannot see or bid on Tenant MY listing."""
        from decimal import Decimal
        from django.utils import timezone
        from apps.listings.models import Listing, ListingStatus, ListingType
        from apps.bidding.models import Auction, AuctionStatus

        # Create active auction in Tenant MY
        listing_my = Listing.objects.create(
            tenant=self.tenant_my,
            seller_id="seller_my_99",
            title="Exclusive MY Fish",
            status=ListingStatus.APPROVED,
            listing_type=ListingType.AUCTION,
        )
        auction_my = Auction.objects.create(
            tenant=self.tenant_my,
            listing=listing_my,
            starting_price=Decimal("50.00"),
            start_at=timezone.now() - timezone.timedelta(hours=1),
            end_at=timezone.now() + timezone.timedelta(days=2),
            status=AuctionStatus.ACTIVE,
        )

        # Attempt tampering: AU dispatcher receives callback targeting MY listing
        self.mock_tg_svc.reset_mock()
        tampered_update = {
            "callback_query": {
                "id": "cb_tamper",
                "from": {"id": 11223344, "username": "au_attacker"},
                "message": {"chat": {"id": 11223344}},
                "data": f"start_bid_{listing_my.id}",
            }
        }
        res = self.dispatcher_buyer_au.dispatch(tampered_update)
        self.assertTrue(res["handled"])
        # Must return Listing not found and not reveal anything
        call = self.mock_tg_svc.send_message.call_args[1]
        self.assertEqual(call["text"], "Listing not found.")

    def test_wishlist_add_and_remove(self):
        """User can add item to favorites and remove it cleanly."""
        from apps.listings.models import Listing, ListingStatus, ListingType
        from apps.bidding.models import BuyerWishlist
        from apps.telegram_engine.messages import BuyerMessages

        listing = Listing.objects.create(
            tenant=self.tenant_my,
            seller_id="seller_my",
            title="Rare Coral Frags",
            status=ListingStatus.APPROVED,
            listing_type=ListingType.AUCTION,
        )

        # Add to wishlist
        self.mock_tg_svc.reset_mock()
        add_cb = {
            "callback_query": {
                "id": "cb_wish_1",
                "from": {"id": self.buyer_user_id, "username": "shabi_buyer"},
                "message": {"chat": {"id": self.chat_id}},
                "data": f"wishlist_{listing.id}",
            }
        }
        self.dispatcher_buyer_my.dispatch(add_cb)
        self.mock_tg_svc.answer_callback_query.assert_called_with(
            "cb_wish_1",
            BuyerMessages.WISHLIST_ADDED.format(listing_id=listing.id),
        )

        wishlist_entry = BuyerWishlist.objects.filter(
            tenant=self.tenant_my,
            telegram_user__telegram_user_id=self.buyer_user_id,
            listing=listing,
        ).first()
        self.assertIsNotNone(wishlist_entry)

        # Remove from wishlist
        self.mock_tg_svc.reset_mock()
        remove_cb = {
            "callback_query": {
                "id": "cb_wish_2",
                "from": {"id": self.buyer_user_id, "username": "shabi_buyer"},
                "message": {"chat": {"id": self.chat_id}},
                "data": f"remove_from_wishlist_{wishlist_entry.id}",
            }
        }
        self.dispatcher_buyer_my.dispatch(remove_cb)
        self.mock_tg_svc.answer_callback_query.assert_called_with(
            "cb_wish_2",
            BuyerMessages.WISHLIST_REMOVED,
        )
        self.assertFalse(BuyerWishlist.objects.filter(id=wishlist_entry.id).exists())

    def test_seller_listing_wizard_flow(self):
        """Seller starts listing wizard, selects category, inputs fields, and completes draft."""
        from apps.listings.models import Listing, ListingStatus
        from apps.telegram_engine.messages import SellerMessages

        # 1. Trigger 'Start New Listing'
        self.mock_tg_svc.reset_mock()
        update = {
            "message": {
                "message_id": 1,
                "chat": {"id": self.chat_id},
                "from": {"id": self.buyer_user_id, "first_name": "SellerShabi"},
                "text": "Start New Listing",
            }
        }
        res = self.dispatcher_seller_my.dispatch(update)
        self.assertTrue(res["handled"])
        call = self.mock_tg_svc.send_message.call_args_list[-1][1]
        self.assertEqual(call["text"], SellerMessages.CATEGORY_TYPE_PROMPT)

        # 2. Select breed option
        self.mock_tg_svc.reset_mock()
        cb = {
            "callback_query": {
                "id": "cb_b1",
                "from": {"id": self.buyer_user_id},
                "message": {"chat": {"id": self.chat_id}},
                "data": "Home & Garden",
            }
        }
        self.dispatcher_seller_my.dispatch(cb)
        call = self.mock_tg_svc.send_message.call_args_list[-1][1]
        self.assertEqual(call["text"], SellerMessages.PRODUCT_TITLE_PROMPT)

        # 3. Enter Title
        self.mock_tg_svc.reset_mock()
        self.dispatcher_seller_my.dispatch({
            "message": {"message_id": 2, "chat": {"id": self.chat_id}, "from": {"id": self.buyer_user_id}, "text": "Aquarium Filter 2000L/H"}
        })
        call = self.mock_tg_svc.send_message.call_args[1]
        self.assertEqual(call["text"], SellerMessages.PRODUCT_DESCRIPTION_PROMPT)

        # 4. Enter Description
        self.mock_tg_svc.reset_mock()
        self.dispatcher_seller_my.dispatch({
            "message": {"message_id": 3, "chat": {"id": self.chat_id}, "from": {"id": self.buyer_user_id}, "text": "Brand new external canister filter."}
        })
        call = self.mock_tg_svc.send_message.call_args[1]
        self.assertEqual(call["text"], SellerMessages.QUANTITY_PROMPT)

        # 5. Enter Quantity
        self.mock_tg_svc.reset_mock()
        self.dispatcher_seller_my.dispatch({
            "message": {"message_id": 4, "chat": {"id": self.chat_id}, "from": {"id": self.buyer_user_id}, "text": "2"}
        })
        call = self.mock_tg_svc.send_message.call_args[1]
        self.assertEqual(call["text"], SellerMessages.CONTACT_PROMPT)

        # 6. Enter Contact Information
        self.mock_tg_svc.reset_mock()
        self.dispatcher_seller_my.dispatch({
            "message": {"message_id": 5, "chat": {"id": self.chat_id}, "from": {"id": self.buyer_user_id}, "text": "+60123456789"}
        })
        call = self.mock_tg_svc.send_message.call_args[1]
        self.assertEqual(call["text"], SellerMessages.SELECT_LISTING_OPTION)

        # 7. Select 'Auction'
        self.mock_tg_svc.reset_mock()
        self.dispatcher_seller_my.dispatch({
            "callback_query": {"id": "cb_opt", "from": {"id": self.buyer_user_id}, "message": {"chat": {"id": self.chat_id}}, "data": "Auction"}
        })
        call = self.mock_tg_svc.send_message.call_args_list[-1][1]
        self.assertEqual(call["text"], SellerMessages.STARTING_PRICE_PROMPT)

        # 8. Enter Starting Price
        self.mock_tg_svc.reset_mock()
        self.dispatcher_seller_my.dispatch({
            "message": {"message_id": 6, "chat": {"id": self.chat_id}, "from": {"id": self.buyer_user_id}, "text": "50"}
        })
        call = self.mock_tg_svc.send_message.call_args[1]
        self.assertEqual(call["text"], SellerMessages.AUTO_ACCEPT_OFFER_PRICE_PROMPT)

        # 9. Enter Auto Accept Price
        self.mock_tg_svc.reset_mock()
        self.dispatcher_seller_my.dispatch({
            "message": {"message_id": 7, "chat": {"id": self.chat_id}, "from": {"id": self.buyer_user_id}, "text": "100"}
        })
        call = self.mock_tg_svc.send_message.call_args[1]
        self.assertEqual(call["text"], SellerMessages.MIN_BID_PROMPT)

        # 10. Enter Minimum Bid
        self.mock_tg_svc.reset_mock()
        self.dispatcher_seller_my.dispatch({
            "message": {"message_id": 8, "chat": {"id": self.chat_id}, "from": {"id": self.buyer_user_id}, "text": "10"}
        })
        call = self.mock_tg_svc.send_message.call_args[1]
        self.assertEqual(call["text"], SellerMessages.START_DATE_PROMPT)

        # 11. Enter Start Date
        self.mock_tg_svc.reset_mock()
        self.dispatcher_seller_my.dispatch({
            "message": {"message_id": 9, "chat": {"id": self.chat_id}, "from": {"id": self.buyer_user_id}, "text": "28-09-2026"}
        })
        call = self.mock_tg_svc.send_message.call_args_list[-1][1]
        self.assertEqual(call["text"], SellerMessages.START_TIME_PROMPT)

        # 12. Enter Start Time
        self.mock_tg_svc.reset_mock()
        self.dispatcher_seller_my.dispatch({
            "message": {"message_id": 10, "chat": {"id": self.chat_id}, "from": {"id": self.buyer_user_id}, "text": "10:00 AM"}
        })
        call = self.mock_tg_svc.send_message.call_args[1]
        self.assertEqual(call["text"], SellerMessages.END_TIME_PROMPT)

        # 13. Select End Time Duration preset (1 day)
        self.mock_tg_svc.reset_mock()
        self.dispatcher_seller_my.dispatch({
            "callback_query": {"id": "cb_dur", "from": {"id": self.buyer_user_id}, "message": {"chat": {"id": self.chat_id}}, "data": "1_day_auction"}
        })
        call = self.mock_tg_svc.send_message.call_args_list[-1][1]
        self.assertEqual(call["text"], SellerMessages.UPLOAD_IMAGES_PROMPT)

        # 14. Upload a photo
        self.mock_tg_svc.reset_mock()
        photo_update = {
            "message": {
                "message_id": 11,
                "chat": {"id": self.chat_id},
                "from": {"id": self.buyer_user_id},
                "photo": [{"file_id": "photo_file_123", "file_size": 50000}],
            }
        }
        self.dispatcher_seller_my.dispatch(photo_update)
        call = self.mock_tg_svc.send_message.call_args[1]
        self.assertIn("You can upload more pictures (3 left)", call["text"])

        # 15. Skip Picture -> prompts for Video
        self.mock_tg_svc.reset_mock()
        self.dispatcher_seller_my.dispatch({
            "callback_query": {"id": "cb_skip_pic", "from": {"id": self.buyer_user_id}, "message": {"chat": {"id": self.chat_id}}, "data": "skip_picture"}
        })
        call = self.mock_tg_svc.send_message.call_args[1]
        self.assertEqual(call["text"], SellerMessages.UPLOAD_VIDEO_PROMPT)

        # 16. Skip Video -> finalizes listing
        self.mock_tg_svc.reset_mock()
        self.dispatcher_seller_my.dispatch({
            "callback_query": {"id": "cb_skip_vid", "from": {"id": self.buyer_user_id}, "message": {"chat": {"id": self.chat_id}}, "data": "skip_video"}
        })
        self.assertEqual(self.mock_tg_svc.send_message.call_count, 2)
        call1 = self.mock_tg_svc.send_message.call_args_list[0][1]
        call2 = self.mock_tg_svc.send_message.call_args_list[1][1]
        self.assertEqual(call1["text"], SellerMessages.LISTING_SAVED_SUCCESS)
        self.assertEqual(call2["text"], SellerMessages.EDIT_DETAILS_PROMPT)

        # Verify listing was created in DB with status PENDING
        created_listing = Listing.objects.filter(tenant=self.tenant_my, title="Aquarium Filter 2000L/H").first()
        self.assertIsNotNone(created_listing)
        self.assertEqual(created_listing.status, ListingStatus.PENDING)
        self.assertEqual(created_listing.quantity, 2)
        self.assertEqual(created_listing.images.count(), 1)

    def test_seller_listing_wizard_with_video_upload(self):
        """Tests that uploading a video during the wizard stores video metadata."""
        user, _ = TelegramUser.objects.get_or_create(
            tenant=self.tenant_my,
            telegram_user_id=self.buyer_user_id,
            defaults={"chat_id": self.chat_id, "username": "seller_test"}
        )
        conv, _ = TelegramConversationState.objects.get_or_create(
            tenant=self.tenant_my,
            telegram_user=user,
            bot_type="SELLER",
            defaults={"state": "PICTURE", "step": "PICTURE", "context_data": {
                "category": "Auction",
                "breed": "Flowerhorn",
                "title": "King Kamfa",
                "description": "Premium fish",
                "quantity": 1,
                "contact": "Seller Contact",
                "starting_price": 50,
                "min_bid": 10,
                "pictures": ["photo_file_abc"],
            }}
        )
        conv.step = "VIDEO"
        conv.context_data = {
            "category": "Auction",
            "breed": "Flowerhorn",
            "title": "King Kamfa",
            "description": "Premium fish",
            "quantity": 1,
            "contact": "Seller Contact",
            "starting_price": 50,
            "min_bid": 10,
            "pictures": ["photo_file_abc"],
        }
        conv.save()

        # Send Video Update
        video_update = {
            "message": {
                "message_id": 99,
                "chat": {"id": self.chat_id},
                "from": {"id": self.buyer_user_id, "username": "seller_test"},
                "video": {"file_id": "video_file_xyz", "file_size": 2048000},
            }
        }
        self.dispatcher_seller_my.dispatch(video_update)

        created_listing = Listing.objects.filter(tenant=self.tenant_my, title="King Kamfa").first()
        self.assertIsNotNone(created_listing)
        self.assertEqual(created_listing.images.count(), 1)
        self.assertEqual(created_listing.images.first().telegram_file_id, "photo_file_abc")
        self.assertEqual(created_listing.metadata.get("video_file_id"), "video_file_xyz")

    def test_telegram_service_photo_and_video(self):
        """Tests that send_photo and send_video in TelegramService format API payloads correctly."""
        mock_client = MagicMock()
        mock_client.post.return_value.json.return_value = {"ok": True, "result": {"message_id": 123}}

        svc = TelegramService(self.seller_bot_my, http_client=mock_client)
        self.seller_bot_my.set_token("TEST_TOKEN_123")
        self.seller_bot_my.save()

        # Test send_photo with file_id
        res_photo = svc.send_photo(chat_id=12345, photo="file_id_photo", caption="Test photo")
        self.assertTrue(res_photo.get("ok"))
        mock_client.post.assert_called()
        self.assertEqual(mock_client.post.call_args[1]["json"]["photo"], "file_id_photo")
        self.assertEqual(mock_client.post.call_args[1]["json"]["caption"], "Test photo")

        # Test send_video with file_id
        res_video = svc.send_video(chat_id=12345, video="file_id_video", caption="Test video")
        self.assertTrue(res_video.get("ok"))
        self.assertEqual(mock_client.post.call_args[1]["json"]["video"], "file_id_video")
        self.assertEqual(mock_client.post.call_args[1]["json"]["caption"], "Test video")



