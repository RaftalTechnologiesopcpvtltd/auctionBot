"""Comprehensive tests for multi-tenant Telegram Engine, webhook security, and update dispatching."""
import json
from unittest.mock import MagicMock, patch
from django.test import TestCase, Client
from apps.tenants.models import Tenant
from apps.telegram_engine.models import TelegramBotConfig, TelegramUser, TelegramUpdateLog, BotType
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

    def test_seller_registration_full_flow(self):
        """Seller registers through conversational questionnaire and profile is created."""
        from apps.listings.models import Seller, SellerStatus

        # 1. /start command
        update_start = {
            "update_id": 1001,
            "message": {
                "message_id": 1,
                "from": self.from_user,
                "chat": {"id": self.chat_id},
                "text": "/start",
            },
        }
        res = self.dispatcher.dispatch(update_start)
        self.assertTrue(res["handled"])
        self.mock_tg_svc.send_message.assert_called()

        # 2. Click Register callback
        update_cb = {
            "update_id": 1002,
            "callback_query": {
                "id": "cb_reg_01",
                "from": self.from_user,
                "message": {"message_id": 1, "chat": {"id": self.chat_id}},
                "data": "seller_register",
            },
        }
        res_cb = self.dispatcher.dispatch(update_cb)
        self.assertEqual(res_cb["step"], "BUSINESS_NAME")

        # 3. Enter Business Name
        self.dispatcher.dispatch({
            "update_id": 1003,
            "message": {"message_id": 2, "from": self.from_user, "chat": {"id": self.chat_id}, "text": "Aquatic Paradise MY"},
        })

        # 4. Enter Contact Name
        self.dispatcher.dispatch({
            "update_id": 1004,
            "message": {"message_id": 3, "from": self.from_user, "chat": {"id": self.chat_id}, "text": "Ahmad Razak"},
        })

        # 5. Enter Phone
        self.dispatcher.dispatch({
            "update_id": 1005,
            "message": {"message_id": 4, "from": self.from_user, "chat": {"id": self.chat_id}, "text": "+60123456789"},
        })

        # 6. Enter Email
        self.dispatcher.dispatch({
            "update_id": 1006,
            "message": {"message_id": 5, "from": self.from_user, "chat": {"id": self.chat_id}, "text": "ahmad@aquaticparadise.my"},
        })

        # 7. Enter Address
        res_final = self.dispatcher.dispatch({
            "update_id": 1007,
            "message": {"message_id": 6, "from": self.from_user, "chat": {"id": self.chat_id}, "text": "Kulai, Johor, Malaysia"},
        })
        self.assertTrue(res_final["handled"])

        # Assert Seller record created
        seller = Seller.objects.get(tenant=self.tenant_my, seller_id=str(self.chat_id))
        self.assertEqual(seller.business_name, "Aquatic Paradise MY")
        self.assertEqual(seller.contact_name, "Ahmad Razak")
        self.assertEqual(seller.phone, "+60123456789")
        self.assertEqual(seller.email, "ahmad@aquaticparadise.my")
        self.assertEqual(seller.status, SellerStatus.ACTIVE)

    def test_listing_wizard_flow_and_submission(self):
        """Registered seller creates listing with photos, reviews draft, and submits for approval."""
        from apps.listings.models import Seller, Listing, ListingStatus, ListingImage

        # Create registered seller
        tg_user = TelegramUser.objects.create(
            tenant=self.tenant_my,
            telegram_user_id=self.chat_id,
            chat_id=self.chat_id,
            username="ahmad_seller",
            first_name="Ahmad",
        )
        Seller.objects.create(
            tenant=self.tenant_my,
            telegram_user=tg_user,
            seller_id=str(self.chat_id),
            business_name="Aquatic Paradise MY",
            contact_name="Ahmad Razak",
            phone="+60123456789",
        )

        # 1. Trigger create listing callback
        self.dispatcher.dispatch({
            "update_id": 2001,
            "callback_query": {
                "id": "cb_list_01",
                "from": self.from_user,
                "message": {"message_id": 1, "chat": {"id": self.chat_id}},
                "data": "create_listing",
            },
        })

        # 2. Title
        self.dispatcher.dispatch({
            "update_id": 2002,
            "message": {"message_id": 2, "from": self.from_user, "chat": {"id": self.chat_id}, "text": "High Grade Super Red Betta Pair"},
        })

        # 3. Description
        self.dispatcher.dispatch({
            "update_id": 2003,
            "message": {"message_id": 3, "from": self.from_user, "chat": {"id": self.chat_id}, "text": "Proven breeder pair, 3.5 months old, fed live daphnia."},
        })

        # 4. Category
        self.dispatcher.dispatch({
            "update_id": 2004,
            "callback_query": {
                "id": "cb_cat_01",
                "from": self.from_user,
                "message": {"message_id": 3, "chat": {"id": self.chat_id}},
                "data": "cat_select:Betta",
            },
        })

        # 5. Starting Price
        self.dispatcher.dispatch({
            "update_id": 2005,
            "message": {"message_id": 4, "from": self.from_user, "chat": {"id": self.chat_id}, "text": "80.00"},
        })

        # 6. Buy Now Price (optional)
        self.dispatcher.dispatch({
            "update_id": 2006,
            "message": {"message_id": 5, "from": self.from_user, "chat": {"id": self.chat_id}, "text": "150.00"},
        })

        # 7. Quantity
        self.dispatcher.dispatch({
            "update_id": 2007,
            "message": {"message_id": 6, "from": self.from_user, "chat": {"id": self.chat_id}, "text": "1"},
        })

        # 8. Upload Photo
        photo_update = {
            "update_id": 2008,
            "message": {
                "message_id": 7,
                "from": self.from_user,
                "chat": {"id": self.chat_id},
                "photo": [
                    {"file_id": "thumb_123", "width": 100, "height": 100},
                    {"file_id": "highres_123", "width": 800, "height": 800},
                ],
            },
        }
        res_photo = self.dispatcher.dispatch(photo_update)
        self.assertEqual(res_photo["photo_count"], 1)

        # 9. Click Done with photos -> shows review
        self.dispatcher.dispatch({
            "update_id": 2009,
            "callback_query": {
                "id": "cb_done_01",
                "from": self.from_user,
                "message": {"message_id": 7, "chat": {"id": self.chat_id}},
                "data": "listing_images_done",
            },
        })

        # 10. Click Submit Listing
        res_submit = self.dispatcher.dispatch({
            "update_id": 2010,
            "callback_query": {
                "id": "cb_sub_01",
                "from": self.from_user,
                "message": {"message_id": 8, "chat": {"id": self.chat_id}},
                "data": "submit_listing",
            },
        })
        self.assertTrue(res_submit["handled"])
        listing_id = res_submit["listing_id"]

        # Assert DB listing state
        listing = Listing.objects.get(id=listing_id)
        self.assertEqual(listing.tenant, self.tenant_my)
        self.assertEqual(listing.seller_id, str(self.chat_id))
        self.assertEqual(listing.title, "High Grade Super Red Betta Pair")
        self.assertEqual(listing.category, "Betta")
        self.assertEqual(listing.status, ListingStatus.PENDING)
        self.assertEqual(listing.metadata["starting_price"], "80.00")
        self.assertEqual(listing.metadata["buy_now_price"], "150.00")

        # Verify images attached
        self.assertEqual(ListingImage.objects.filter(listing=listing).count(), 1)
        self.assertEqual(ListingImage.objects.get(listing=listing).telegram_file_id, "highres_123")

    def test_draft_recovery_on_start(self):
        """In-progress listing draft is preserved and prompts recovery on subsequent /start."""
        from apps.listings.models import Seller
        from apps.telegram_engine.models import TelegramConversationState, ConversationState

        tg_user = TelegramUser.objects.create(
            tenant=self.tenant_my,
            telegram_user_id=self.chat_id,
            chat_id=self.chat_id,
            username="ahmad_seller",
        )
        Seller.objects.create(
            tenant=self.tenant_my,
            telegram_user=tg_user,
            seller_id=str(self.chat_id),
            business_name="Aquatic Paradise MY",
            contact_name="Ahmad Razak",
            phone="+60123456789",
        )

        conv = TelegramConversationState.objects.create(
            tenant=self.tenant_my,
            telegram_user=tg_user,
            bot_type=BotType.SELLER,
            state=ConversationState.CREATING_LISTING,
            step="STARTING_PRICE",
            context_data={"listing_draft": {"title": "Rare Discus Fish"}},
        )

        # User sends /start
        res = self.dispatcher.dispatch({
            "update_id": 3001,
            "message": {
                "message_id": 1,
                "from": self.from_user,
                "chat": {"id": self.chat_id},
                "text": "/start",
            },
        })
        self.assertEqual(res["action"], "prompt_resume_draft")

        # User clicks resume
        res_resume = self.dispatcher.dispatch({
            "update_id": 3002,
            "callback_query": {
                "id": "cb_res_01",
                "from": self.from_user,
                "message": {"message_id": 1, "chat": {"id": self.chat_id}},
                "data": "resume_listing",
            },
        })
        self.assertEqual(res_resume["action"], "resumed")
        self.assertEqual(res_resume["step"], "STARTING_PRICE")

        # User clicks cancel
        res_cancel = self.dispatcher.dispatch({
            "update_id": 3003,
            "callback_query": {
                "id": "cb_can_01",
                "from": self.from_user,
                "message": {"message_id": 1, "chat": {"id": self.chat_id}},
                "data": "cancel_listing",
            },
        })
        self.assertTrue(res_cancel["handled"])
        conv.refresh_from_db()
        self.assertEqual(conv.state, ConversationState.IDLE)

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


