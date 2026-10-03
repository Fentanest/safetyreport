import configparser
import unittest
from types import SimpleNamespace
from unittest import mock
import bot


class BotAuthorityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        config = configparser.ConfigParser()
        config['TELEGRAM'] = {'chat_id': '123'}
        for patch in (mock.patch.object(bot.settings, 'chat_id', '123'),
                      mock.patch.object(bot.settings, 'config', config)):
            patch.start()
            self.addCleanup(patch.stop)
        self.config = config

    def update(self, chat=123, user=123, kind='private'):
        return SimpleNamespace(effective_chat=SimpleNamespace(id=chat, type=kind),
                               effective_user=SimpleNamespace(id=user),
                               callback_query=mock.AsyncMock(), message=mock.AsyncMock())

    async def test_every_personal_handler_denies_unconfigured_user(self):
        for name in ('start', 'help_command', 'button', 'receive_car_number',
                     'receive_report_number', 'cancel'):
            with self.subTest(handler=name):
                update = self.update(user=456)
                result = await getattr(bot, name)(update, mock.Mock())
                self.assertEqual(result, bot.ConversationHandler.END)
                update.message.reply_text.assert_not_called()
                update.callback_query.edit_message_text.assert_not_called()

    def test_private_chat_requires_chat_and_user_and_groups_need_explicit_users(self):
        self.assertTrue(bot.authorized_update(self.update()))
        self.assertFalse(bot.authorized_update(self.update(chat=456)))
        self.assertFalse(bot.authorized_update(self.update(kind='group')))
        self.config['TELEGRAM']['allowed_user_ids'] = '456'
        self.assertTrue(bot.authorized_update(self.update(kind='group', user=456)))
        self.assertFalse(bot.authorized_update(self.update(kind='group')))

    async def test_standalone_writer_does_not_spawn_and_managed_uses_shared_control(self):
        update = self.update()
        update.callback_query.data = 'start_crawl'
        context = SimpleNamespace(application=SimpleNamespace(bot_data={}))
        with mock.patch('services.crawl_control.start_crawl') as start:
            await bot.button(update, context)
            start.assert_not_called()
            context.application.bot_data['managed_coordinator'] = True
            await bot.button(update, context)
            start.assert_called_once()
            self.assertEqual(start.call_args.kwargs['broadcast_source'], 'telegram')

    async def test_managed_start_failure_stops_initialized_application(self):
        application = mock.Mock(initialize=mock.AsyncMock(), start=mock.AsyncMock(side_effect=RuntimeError('fixture')),
                                stop=mock.AsyncMock(), shutdown=mock.AsyncMock(), running=False)
        application.updater = mock.Mock(start_polling=mock.AsyncMock(), stop=mock.AsyncMock(), running=False)
        with mock.patch.object(bot, 'build_application', return_value=application):
            with self.assertRaises(RuntimeError): await bot.start_managed()
        application.initialize.assert_awaited_once()
        application.shutdown.assert_awaited_once()
